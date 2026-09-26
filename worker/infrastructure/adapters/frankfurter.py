from __future__ import annotations

from datetime import datetime, timezone

import requests


class FrankfurterFxAdapter:
    name = "frankfurter"

    def rate_to_eur(self, base_currency: str) -> tuple[float, str]:
        base = base_currency.upper()
        if base == "EUR":
            return 1.0, datetime.now(timezone.utc).date().isoformat()

        r = requests.get(
            f"https://api.frankfurter.dev/v2/rate/{base}/EUR",
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        rate = data.get("rate")
        if rate is None:
            # fallback blend endpoint
            r2 = requests.get(
                "https://api.frankfurter.dev/v2/latest",
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
