import numpy as np
import pytest

from aridtrial.models import fao56 as F


def test_fao56_example18_brussels_daily_eto():
    """FAO-56 Example 18 (Uccle, 6 July): published ET0 = 3.9 mm/day."""
    doy = 187
    u2 = F.wind_at_2m(10 / 3.6, 10.0)
    rs = F.rs_from_sunshine(9.25, 50.80, doy)
    assert float(F.extraterrestrial_radiation(50.80, doy)) == pytest.approx(41.1, abs=0.2)  # paper: 41.09
    assert float(rs) == pytest.approx(22.1, abs=0.2)  # paper: 22.07
    et0 = float(F.eto_pm_daily(21.5, 12.3, 84, 63, u2, rs, 50.80, 100.0, doy))
    assert et0 == pytest.approx(3.9, abs=0.1)


def test_atmospheric_pressure_and_gamma_at_sea_level():
    assert F.atm_pressure(0.0) == pytest.approx(101.3, abs=0.05)
    assert F.psychrometric_constant(101.3) == pytest.approx(0.0674, abs=1e-3)


def test_svp_known_value():
    assert float(F.svp(20.0)) == pytest.approx(2.338, abs=0.005)  # FAO-56 Table 2.3


def test_water_balance_conserves_and_bounds():
    rz = F.RootZone(theta_fc=0.14, theta_wp=0.06, zr_m=0.6)
    n = 120
    wb = F.water_balance(np.full(n, 6.0), np.zeros(n), np.full(n, 0.5), rz, irrigate=True, mad=0.2)
    assert (wb["dr_mm"] >= 0).all() and (wb["dr_mm"] <= rz.taw + 1e-9).all()
    assert (wb["ks"].between(0, 1)).all()
    # with irrigation on, applied net water ~ crop demand (steady state)
    assert wb["irr_net_mm"].sum() == pytest.approx(wb["etc_mm"].sum(), rel=0.1)


def test_stress_coefficient_shape():
    rz = F.RootZone(0.14, 0.06, 0.6, p=0.5)
    assert F.stress_coefficient(rz.raw * 0.9, rz) == 1.0
    assert F.stress_coefficient(rz.taw, rz) == 0.0
    assert 0 < F.stress_coefficient((rz.raw + rz.taw) / 2, rz) < 1
