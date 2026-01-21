import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from app.calc.engine import simulate

app = FastAPI()

# Cache in-memory (nota: si svuota al riavvio del server)
RESULTS_CACHE: dict[str, dict[str, Any]] = {}

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _parse_bool(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on", "y", "si", "sì")


class SimulatePayload(BaseModel):
    # Base
    age_now: int = Field(..., ge=18, le=80)
    age_target: int = Field(..., ge=19, le=90)
    ral_now: float = Field(..., gt=0)
    year_hiring: int = Field(..., ge=1980, le=2100)
    ral_growth_pct: float = Field(0.0, ge=-50.0, le=50.0)
    tfr_existing: float = Field(0.0, ge=0)

    # TFR in azienda
    inflation_avg_pct: float = Field(3.06, ge=-5.0, le=20.0)
    sep_tax_rate_pct: float = Field(26.0, ge=0.0, le=60.0)

    # PIP
    scenario: str = Field("intermedio")
    pip_line: str = Field("life_cycle")  # garantita|bilanciata|azionaria|life_cycle
    ltc_enabled: bool = Field(False)

    # Versamenti volontari + beneficio fiscale
    voluntary_annual: float = Field(0.0, ge=0.0)
    taxable_income: Optional[float] = Field(None)  # imponibile stimato, facoltativo
    irpef_model: str = Field("2026")  # "2025" o "2026"
    cost_profile: str = Field("post_2023")  # post_2023 | 2020_2023


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # Se non hai home.html, puoi puntare direttamente al simulatore
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/simulatore", response_class=HTMLResponse)
def simulator_page(request: Request):
    # One-page, senza risultati iniziali
    return templates.TemplateResponse(
        "simulator.html",
        {
            "request": request,
            "initial_payload": None,
            "initial_result": None,
            "rid": None,
            "share_url": None,
        },
    )


@app.get("/r/{rid}", response_class=HTMLResponse)
def share_page(request: Request, rid: str):
    cached = RESULTS_CACHE.get(rid)
    if not cached:
        raise HTTPException(
            status_code=404,
            detail="Simulazione non trovata (cache scaduta o server riavviato).",
        )

    base = str(request.base_url).rstrip("/")
    share_url = f"{base}/r/{rid}"

    return templates.TemplateResponse(
        "simulator.html",
        {
            "request": request,
            "initial_payload": cached.get("payload"),
            "initial_result": cached.get("result"),
            "rid": rid,
            "share_url": share_url,
        },
    )


@app.post("/api/simulate", response_class=JSONResponse)
def api_simulate(request: Request, payload: SimulatePayload):
    # Calcolo
    result = simulate(**payload.model_dump())

    # Salvo in cache per share/pdf
    rid = uuid.uuid4().hex[:12]
    RESULTS_CACHE[rid] = {
        "payload": payload.model_dump(),
        "result": result,
    }

    base = str(request.base_url).rstrip("/")
    share_url = f"{base}/r/{rid}"
    pdf_url = f"{base}/simulatore/pdf/{rid}"

    return JSONResponse(
        {
            "rid": rid,
            "share_url": share_url,
            "pdf_url": pdf_url,
            "result": result,
        }
    )


@app.get("/simulatore/pdf-view/{rid}", response_class=HTMLResponse)
def pdf_view(request: Request, rid: str):
    cached = RESULTS_CACHE.get(rid)
    if not cached:
        raise HTTPException(
            status_code=404,
            detail="Simulazione non trovata (cache scaduta o server riavviato).",
        )

    base = str(request.base_url).rstrip("/")
    share_url = f"{base}/r/{rid}"

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "result": cached["result"],
            "rid": rid,
            "share_url": share_url,
            "for_pdf": True,
        },
    )


@app.get("/simulatore/pdf/{rid}")
def export_pdf(request: Request, rid: str):
    # Renderizzo una pagina HTML "print-friendly" e la converto in PDF con Playwright
    base = str(request.base_url).rstrip("/")
    url = f"{base}/simulatore/pdf-view/{rid}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(url, wait_until="networkidle")
        # Aspetto che il grafico sia pronto
        page.wait_for_function("window.__chartReady === true", timeout=15000)

        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()

    headers = {"Content-Disposition": f'attachment; filename="simulazione_{rid}.pdf"'}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
