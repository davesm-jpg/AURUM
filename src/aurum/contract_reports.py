from dataclasses import asdict
from datetime import datetime

from aurum.contracts import CONTRACT_VERSION, DEFAULT_ROLL_POLICY, ContractCatalog, RollPolicy
from aurum.instruments import MgcContract
from aurum.storage import DatasetStore


def save_contract_assessment(
    store: DatasetStore,
    catalog: ContractCatalog,
    as_of: datetime,
    current: MgcContract | None = None,
    policy: RollPolicy = DEFAULT_ROLL_POLICY,
) -> str:
    decision = catalog.decide(as_of, current, policy)
    return store.save_analysis(
        "contract_roll_assessment",
        {
            "contract_version": CONTRACT_VERSION,
            "catalog": catalog.as_dict(),
            "policy": asdict(policy),
            "as_of": as_of.isoformat(),
            "current": current.code if current else None,
        },
        {"data_kind": catalog.data_kind, **decision.as_dict()},
    )
