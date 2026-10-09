"""FICTIONAL sample recipients, used only to preview template layout and in tests. Never sent."""

from __future__ import annotations

from ..compose.render import Recipient
from ..models import Claim, DraftSlots
from .verify_claims import Source

SAMPLES: dict[str, dict] = {
    "initial_a": {
        "recipient": Recipient(first="Alex", last="Rivera", role="professor", university="Stanford University",
                               campus="Stanford", local=True),
        "source": Source(
            id=1, type="paper", title="Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs", year=2025,
            url="https://example.edu/rivera/paper",
            snippet="We present a compliant socket interface that adapts to residual-limb volume change and "
                    "reduces fitting iterations for low-cost prosthetic limbs."),
        "slots": DraftSlots(
            subject_formula="S1", subject_topic="low-cost prosthetic limbs", subject_year=2025,
            p1='I read your 2025 paper "Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs" on a compliant '
               "socket that adapts to residual-limb volume change.",
            p2="Because our ReStep design is also a low-cost adjustable limb, I wondered how your interface "
               "handles fit across different users.",
            credential_fact_ids=["F_RESTEP", "F_FTC_CAD"], task="CAD or fixture design",
            claims=[Claim(text='your 2025 paper "Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs" on a '
                               "compliant socket that adapts to residual-limb volume change", source_id=1),
                    Claim(text="our ReStep design is also a low-cost adjustable limb", profile_fact_id="F_RESTEP"),
                    Claim(text="how your interface handles fit across different users", source_id=1)],
            confidence=0.9),
    },
    "initial_b": {
        "recipient": Recipient(first="Priya", last="Nair", role="postdoc", university="University of Washington",
                               campus="", local=False),
        "source": Source(
            id=2, type="paper", title="Low-Power Turbidity Sensing for Distributed Water Monitoring", year=2025,
            url="https://example.edu/nair/paper",
            snippet="We describe a low-power optical turbidity node with on-device calibration for distributed "
                    "water monitoring deployments."),
        "slots": DraftSlots(
            subject_formula="S2", subject_topic="turbidity sensing", subject_year=None,
            p1='I read your 2025 paper "Low-Power Turbidity Sensing for Distributed Water Monitoring" on an '
               "optical node with on-device calibration.",
            p2="I built a turbidity sensor for stormwater, so I am curious how your calibration holds up between "
               "deployments.",
            credential_fact_ids=["F_LUNA", "F_GLASSES"], task="sensor firmware",
            claims=[Claim(text='your 2025 paper "Low-Power Turbidity Sensing for Distributed Water Monitoring" '
                               "on an optical node with on-device calibration", source_id=2),
                    Claim(text="I built a turbidity sensor for stormwater", profile_fact_id="F_LUNA"),
                    Claim(text="how your calibration holds up between deployments", source_id=2)],
            confidence=0.9),
    },
    "initial_c": {
        "recipient": Recipient(first="Sam", last="Okafor", role="grad_student", university="Carnegie Mellon University",
                               campus="", local=False, informal=True),
        "source": Source(
            id=3, type="paper", title="Few-Shot Defect Detection for Small Manufacturing Datasets", year=2026,
            url="https://example.edu/okafor/paper",
            snippet="We study few-shot defect detection when only a handful of labeled manufacturing images exist, "
                    "using lightweight vision models."),
        "slots": DraftSlots(
            subject_formula="S4", subject_topic="defect detection", subject_year=None,
            p1='I read your 2026 paper "Few-Shot Defect Detection for Small Manufacturing Datasets" on lightweight '
               "vision models with few labels.",
            p2="I built a classifier-based vision project on an ESP32-CAM, so I would like to learn how you chose "
               "your baselines.",
            credential_fact_ids=["F_EPA", "F_INNO"], task="a baseline ML experiment",
            claims=[Claim(text='your 2026 paper "Few-Shot Defect Detection for Small Manufacturing Datasets" on '
                               "lightweight vision models with few labels", source_id=3),
                    Claim(text="I built a classifier-based vision project on an ESP32-CAM", profile_fact_id="F_GLASSES"),
                    Claim(text="how you chose your baselines", source_id=3)],
            confidence=0.9),
    },
}
