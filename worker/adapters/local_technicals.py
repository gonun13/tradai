from __future__ import annotations

from typing import Iterable

from adapters.base import AdapterMetadata
from technicals import BarClose, compute_features


class LocalTechnicalsAdapter:
    name = "local"

    @property
    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            provider=self.name,
            regions=frozenset({"us", "eu"}),
            instrument_kinds=frozenset({"equity", "etf"}),
            operations=frozenset({"technicals"}),
            enabled=True,
        )

    def get_technicals(
        self, symbol: str, bars: Iterable[BarClose]
    ) -> dict:
        return compute_features(list(bars))
