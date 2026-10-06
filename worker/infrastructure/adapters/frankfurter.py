from __future__ import annotations

from datetime import datetime, timezone

import requests


class FrankfurterFxAdapter:
    name = "frankfurter"

    def rate_to_eur(self, base_currency: str, on_date: str | None = None) -> tuple[float, str]:
        """Latest base -> EUR rate, or the rate published for `on_date` (YYYY-MM-DD, 0031)."""
        base = base_currency.upper()
        if base == "EUR":
            return 1.0, on_date or datetime.now(timezone.utc).date().isoformat()

        r = requests.get(
            f"https://api.frankfurter.dev/v2/rate/{base}/EUR",
            params={"date": on_date} if on_date else None,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        rate = data.get("rate")
        if rate is None:
            # fallback blend endpoint
            r2 = requests.get(
                f"https://api.frankfurter.dev/v1/{on_date or 'latest'}",
                params={"base": base, "symbols": "EUR"},
                timeout=30,
            )
            r2.raise_for_status()
            data2 = r2.json()
            rate = (data2.get("rates") or {}).get("EUR")
            as_of = data2.get("date") or datetime.now(timezone.utc).date().isoformat()
        else:
            as_of = data.get("date") or datetime.now(timezone.utc).date().isoformat()

        if rate is None:
            raise RuntimeError(f"Frankfurter returned no EUR rate for {base}: {data}")
        return float(rate), str(as_of)
