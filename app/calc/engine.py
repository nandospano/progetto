from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

PipLine = Literal["garantita", "bilanciata", "azionaria", "life_cycle"]
Scenario = Literal["prudente", "intermedio", "dinamico"]
IrpefModel = Literal["2025", "2026"]
CostProfile = Literal["post_2023", "2020_2023"]

DEDUCT_LIMIT_EUR = 5164.57  # plafond deducibilità contributi (non include TFR)


@dataclass
class CostConfig:
    adhesion_fee_eur: float
    load_fee_pct: float
    load_fee_ltc_pct: float
    garantita_indirect_pct: float
    bilanciata_indirect_pct: float
    azionaria_indirect_pct: float


COSTS: dict[CostProfile, CostConfig] = {
    # Scheda "I costi" in vigore dal 31/03/2025, per adesioni dal 24/07/2023
    "post_2023": CostConfig(
        adhesion_fee_eur=10.0,
        load_fee_pct=3.0,
        load_fee_ltc_pct=4.5,
        garantita_indirect_pct=1.30,
        bilanciata_indirect_pct=1.55,
        azionaria_indirect_pct=1.75,
    ),
    # Per adesioni dal 24/02/2020 al 23/07/2023 (stessa scheda riporta 4% e Garantita 1,50)
    "2020_2023": CostConfig(
        adhesion_fee_eur=10.0,
        load_fee_pct=4.0,
        load_fee_ltc_pct=4.0,  # in quel profilo non è evidenziato 4,5% LTC; mantengo 4% coerente al testo
        garantita_indirect_pct=1.50,
        bilanciata_indirect_pct=1.55,
        azionaria_indirect_pct=1.75,
    ),
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _tfr_reval_rate_pct(inflation_avg_pct: float) -> float:
    # Rivalutazione TFR: 1,5% + 75% inflazione
    return 1.5 + 0.75 * inflation_avg_pct


def _pip_line_for_age(pip_line: PipLine, age: int) -> PipLine:
    if pip_line != "life_cycle":
        return pip_line
    if age <= 49:
        return "azionaria"
    if 50 <= age <= 59:
        return "bilanciata"
    return "garantita"


def _pip_indirect_fee_pct(line: PipLine, cfg: CostConfig) -> float:
    if line == "garantita":
        return cfg.garantita_indirect_pct
    if line == "bilanciata":
        return cfg.bilanciata_indirect_pct
    return cfg.azionaria_indirect_pct


def _scenario_return_gross_pct(scenario: Scenario) -> float:
    # Rendimenti attesi (parametrizzabili in futuro):
    # - prudente: più conservativo
    # - intermedio: medio
    # - dinamico: più aggressivo
    if scenario == "prudente":
        return 3.0
    if scenario == "dinamico":
        return 6.0
    return 4.5


def _pip_final_tax_pct(years_participation: int) -> float:
    # 15% che scende di 0,3% per ogni anno oltre il 15°, fino a 9%
    if years_participation <= 15:
        return 15.0
    reduction = (years_participation - 15) * 0.3
    return _clamp(15.0 - reduction, 9.0, 15.0)


def _irpef_brackets(model: IrpefModel):
    # Modello semplice per stimare aliquota marginale.
    # 2025: 23% - 35% - 43% (approssimazione)
    # 2026: 23% - 33% - 43% (da news istituzionali sulla manovra; teniamo come opzione)
    if model == "2026":
        return [(28000.0, 0.23), (50000.0, 0.33), (10**18, 0.43)]
    return [(28000.0, 0.23), (50000.0, 0.35), (10**18, 0.43)]


def _marginal_irpef_rate(taxable_income: float, model: IrpefModel) -> float:
    for ceiling, rate in _irpef_brackets(model):
        if taxable_income <= ceiling:
            return rate
    return 0.43


def simulate(
    # Base
    age_now: int,
    age_target: int,
    ral_now: float,
    year_hiring: int,
    ral_growth_pct: float,
    tfr_existing: float,
    # TFR in azienda
    inflation_avg_pct: float,
    sep_tax_rate_pct: float,
    # PIP
    scenario: Scenario,
    pip_line: PipLine,
    ltc_enabled: bool,
    # Volontari + fisco
    voluntary_annual: float = 0.0,
    taxable_income: Optional[float] = None,
    irpef_model: IrpefModel = "2026",
    cost_profile: CostProfile = "post_2023",
) -> dict:
    if age_target <= age_now:
        raise ValueError("age_target deve essere maggiore di age_now")

    years = int(age_target - age_now)
    growth = ral_growth_pct / 100.0

    # --- TFR in azienda ---
    tfr_balance = float(tfr_existing)
    tfr_principal = float(
        tfr_existing
    )  # approssimazione: trattiamo l'esistente come capitale
    tfr_reval_tax_total = 0.0

    reval_rate_pct = _tfr_reval_rate_pct(inflation_avg_pct)
    reval_rate = reval_rate_pct / 100.0

    annual_rows_tfr = []
    series_ages = []
    series_tfr = []

    # --- PIP (Alleata Previdenza) ---
    cfg = COSTS[cost_profile]

    pip_balance = 0.0
    pip_capital = 0.0  # base imponibile finale (approssimazione): capitale effettivamente investito (post costi)
    pip_costs_total = 0.0
    pip_load_fees_total = 0.0
    pip_mgmt_fees_total = 0.0
    pip_tax_returns_total = 0.0

    pip_return_gross_pct = _scenario_return_gross_pct(scenario)
    pip_return_tax_rate = 0.20  # imposta sui rendimenti (semplificazione: 20%)
    annual_rows_pip = []
    series_pip = []

    # Beneficio fiscale (solo su versamenti volontari; TFR escluso)
    if taxable_income is None:
        taxable_income = ral_now  # fallback: imponibile ~ RAL (approssimazione)
    tax_saving_total = 0.0
    tax_saving_first_year = 0.0
    marginal_rate = _marginal_irpef_rate(float(taxable_income), irpef_model)

    # Applico spesa di adesione una sola volta (se c'è almeno un versamento)
    adhesion_fee_applied = False

    for i in range(years):
        age = age_now + i
        ral = ral_now * ((1.0 + growth) ** i)

        # Quota TFR annuale (approssimazione standard 1/13,5)
        tfr_quota = ral / 13.5

        # --- TFR azienda: rivalutazione su saldo iniziale anno + tassazione 17% sulla rivalutazione ---
        tfr_reval_gross = tfr_balance * reval_rate
        tfr_reval_tax = tfr_reval_gross * 0.17
        tfr_reval_net = tfr_reval_gross - tfr_reval_tax

        tfr_reval_tax_total += tfr_reval_tax
        tfr_balance += tfr_reval_net

        # Aggiungo quota annua
        tfr_principal += tfr_quota
        tfr_balance += tfr_quota

        annual_rows_tfr.append(
            {
                "year_index": i + 1,
                "age": age + 1,
                "ral": ral,
                "tfr_quota": tfr_quota,
                "tfr_reval_gross": tfr_reval_gross,
                "tfr_reval_tax": tfr_reval_tax,
                "tfr_balance": tfr_balance,
            }
        )

        # --- PIP: contribuzione = TFR conferito + volontario ---
        contrib_tfr = tfr_quota
        contrib_vol = float(voluntary_annual)
        contrib_total = contrib_tfr + contrib_vol

        load_fee_pct = (
            cfg.load_fee_ltc_pct if ltc_enabled else cfg.load_fee_pct
        ) / 100.0
        load_fee = contrib_total * load_fee_pct
        invested = contrib_total - load_fee

        pip_load_fees_total += load_fee
        pip_costs_total += load_fee

        # Spese adesione una tantum
        adhesion_fee = 0.0
        if (not adhesion_fee_applied) and (contrib_total > 0.0):
            adhesion_fee = cfg.adhesion_fee_eur
            adhesion_fee_applied = True
            pip_costs_total += adhesion_fee

        # Accredito (post costi diretti)
        net_in = invested - adhesion_fee
        if net_in < 0:
            # se il versamento non copre i costi iniziali, porto a zero
            net_in = 0.0

        pip_balance += net_in
        pip_capital += net_in

        # Costi indiretti (gestione) + rendimento
        effective_line = _pip_line_for_age(pip_line, age)
        mgmt_fee_pct = _pip_indirect_fee_pct(effective_line, cfg) / 100.0

        gross_return = pip_balance * (pip_return_gross_pct / 100.0)
        mgmt_fee_amount = pip_balance * mgmt_fee_pct

        pip_mgmt_fees_total += mgmt_fee_amount
        pip_costs_total += mgmt_fee_amount

        growth_before_tax = gross_return - mgmt_fee_amount

        # Imposta rendimenti (semplificazione: su crescita positiva netta)
        return_tax = max(0.0, growth_before_tax) * pip_return_tax_rate
        pip_tax_returns_total += return_tax

        pip_balance += growth_before_tax - return_tax

        # Beneficio fiscale (solo volontari, max plafond)
        deductible = min(contrib_vol, DEDUCT_LIMIT_EUR)
        tax_saving = deductible * marginal_rate
        tax_saving_total += tax_saving
        if i == 0:
            tax_saving_first_year = tax_saving

        annual_rows_pip.append(
            {
                "year_index": i + 1,
                "age": age + 1,
                "ral": ral,
                "contribution_total": contrib_total,
                "contribution_tfr": contrib_tfr,
                "contribution_voluntary": contrib_vol,
                "load_fee": load_fee,
                "adhesion_fee": adhesion_fee,
                "mgmt_fee": mgmt_fee_amount,
                "gross_return": gross_return,
                "return_tax": return_tax,
                "line_effective": effective_line,
                "balance": pip_balance,
            }
        )

        series_ages.append(age + 1)
        series_tfr.append(tfr_balance)
        series_pip.append(pip_balance)

    # TFR: tassa finale separata (approssimazione: su capitale/principal, rivalutazioni già tassate 17% lungo il percorso)
    tfr_final_tax = tfr_principal * (sep_tax_rate_pct / 100.0)
    tfr_tax_total = tfr_reval_tax_total + tfr_final_tax
    tfr_net = tfr_balance - tfr_final_tax

    # PIP: tassa finale agevolata (approssimazione: su capitale investito; rendimenti già tassati in corso d'opera)
    pip_final_tax_pct = _pip_final_tax_pct(years)
    pip_final_tax = pip_capital * (pip_final_tax_pct / 100.0)
    pip_tax_total = pip_tax_returns_total + pip_final_tax
    pip_net = pip_balance - pip_final_tax

    winner = "PIP" if pip_net > tfr_net else "TFR"
    advantage = abs(pip_net - tfr_net)

    return {
        "years": years,
        "winner": winner,
        "advantage": advantage,
        "tfr": {
            "gross": tfr_balance,
            "principal": tfr_principal,
            "reval_tax_total": tfr_reval_tax_total,
            "final_tax": tfr_final_tax,
            "tax_total": tfr_tax_total,
            "net": tfr_net,
        },
        "pip": {
            "gross": pip_balance,
            "capital": pip_capital,
            "costs_total": pip_costs_total,
            "costs_load_total": pip_load_fees_total,
            "costs_mgmt_total": pip_mgmt_fees_total,
            "tax_returns_total": pip_tax_returns_total,
            "final_tax": pip_final_tax,
            "tax_total": pip_tax_total,
            "net": pip_net,
        },
        "tax_benefit": {
            "deduct_limit_eur": DEDUCT_LIMIT_EUR,
            "marginal_rate_pct_est": round(marginal_rate * 100.0, 2),
            "saving_first_year": tax_saving_first_year,
            "saving_total": tax_saving_total,
            "voluntary_annual": float(voluntary_annual),
            "taxable_income_used": float(taxable_income),
            "irpef_model": irpef_model,
        },
        "assumptions": {
            "inflation_avg_pct": inflation_avg_pct,
            "tfr_reval_rate_pct": round(reval_rate_pct, 3),
            "sep_tax_rate_pct": sep_tax_rate_pct,
            "scenario": scenario,
            "pip_line": pip_line,
            "pip_return_gross_pct": pip_return_gross_pct,
            "pip_return_tax_rate_pct": 20.0,
            "pip_load_fee_pct": cfg.load_fee_ltc_pct
            if ltc_enabled
            else cfg.load_fee_pct,
            "pip_final_tax_pct": round(pip_final_tax_pct, 2),
            "ltc_enabled": bool(ltc_enabled),
            "cost_profile": cost_profile,
        },
        "series": {
            "ages": series_ages,
            "tfr": series_tfr,
            "pip": series_pip,
        },
        "annual_rows_tfr": annual_rows_tfr,
        "annual_rows_pip": annual_rows_pip,
    }
