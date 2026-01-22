# Progetto simulatore TFR/PIP

Questa guida serve per **allineare la tua cartella locale, GitHub e Render** e mettere l'app online senza confusione.

## ✅ 1) Allinea la tua cartella locale con GitHub

Apri un terminale **dentro la cartella del progetto** sul tuo PC e fai:

```bash
git status
```

Se vedi file modificati o non tracciati:

```bash
git add .
git commit -m "Sync locale con GitHub"
git push
```

Se invece vuoi scaricare gli ultimi aggiornamenti da GitHub:

```bash
git pull
```

## ✅ 2) Verifica che GitHub abbia i file giusti

Controlla sul tuo repo GitHub che esistano questi file:

- `requirements.txt`
- `app/main.py`
- `app/templates/`

Se li vedi, significa che GitHub è **allineato**.

## ✅ 3) Configura Render (una volta sola)

Nella dashboard Render (Web Service):

**Build Command** (opzionale):
```bash
pip install -r requirements.txt
```

**Start Command**:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 10000
```

**Branch**: assicurati che sia quello in cui hai fatto il `push` (di solito `main`).

**Root Directory**: lascia vuoto (il progetto è alla root).

## ✅ 4) Re-deploy su Render

Fai **Manual Deploy** dal pannello Render.

Se tutto è allineato, il deploy deve andare a buon fine.

## 🔎 FAQ rapida

**Render non vede le modifiche?**
- Hai fatto `git push`?
- Render sta usando lo stesso branch?

**Errore su Playwright/PDF?**
- In Render il PDF potrebbe richiedere anche:
  ```bash
  python -m playwright install
  ```

## ✅ Dipendenze

Il progetto usa queste librerie (da `requirements.txt`):

- FastAPI
- Uvicorn
- Jinja2
- Playwright

