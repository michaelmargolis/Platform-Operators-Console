#test_poc_ride_selector_v237.py ver 2
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_sim_module():
    # Load the adapter in isolation so this focused display test does not need
    # Qt, sockets or a complete NoLimits installation.
    _stub("core.sim_interface", SimInterface=object)
    _stub(
        "core.types",
        ExperienceState=types.SimpleNamespace(),
        SimSample=object,
        SimStatus=object,
    )
    _stub("sims", __path__=[])
    nl2 = _stub("sims.nolimits2", __path__=[])
    config = _stub(
        "sims.nolimits2.config",
        FAN_CONFIG={},
        SERVICE_PERIOD_MS=50,
    )
    ride_config = _stub(
        "sims.nolimits2.ride_config",
        SHOW_VARIANT_MODIFIERS=True,
        PARKS={
            "forest": {"name": "Forest Mountain Park"},
            "blackbird": {"name": "Blackbird"},
        },
    )
    angular_cues = _stub("sims.nolimits2.angular_cues")
    nl2.config = config
    nl2.ride_config = ride_config
    nl2.angular_cues = angular_cues
    _stub("sims.nolimits2.variant_store", VariantStore=object)
    _stub("sims.nolimits2.nl2_protocol", NL2Protocol=object, NL2Telemetry=object)
    _stub("sims.nolimits2.station_modes", StationModeManager=object)

    spec = importlib.util.spec_from_file_location(
        "_poc_v237_nl2_sim", ROOT / "sims" / "nolimits2" / "sim.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dynamic_candidate_display_includes_strict_or_relaxed():
    module = _load_sim_module()
    sim = object.__new__(module.Sim)
    sim._visible_indices = (0, 1, 2)
    sim._experiences = (
        "Forest Mountain Park [Dynamic A]",
        "Forest Mountain Park [Dynamic B]",
        "Blackbird",
    )
    sim._park_keys = ("forest", "forest", "blackbird")

    sim.set_dynamic_qualification_targets({
        "Forest Mountain Park [Dynamic A]": "strict",
        "Forest Mountain Park [Dynamic B]": "relaxed5",
    })

    assert sim.display_name_for_experience(0) == "Forest Mountain Park [Dynamic A] Strict"
    assert sim.display_name_for_experience(1) == "Forest Mountain Park [Dynamic B] Relaxed"
    assert sim.display_name_for_experience(2) == "Blackbird"
    assert sim.get_experience_display_names() == (
        "Forest Mountain Park [Dynamic A] Strict",
        "Forest Mountain Park [Dynamic B] Relaxed",
        "Blackbird",
    )


def test_unknown_target_is_not_guessed():
    module = _load_sim_module()
    sim = object.__new__(module.Sim)
    sim._visible_indices = (0,)
    sim._experiences = ("Forest Mountain Park [Dynamic A]",)
    sim._park_keys = ("forest",)
    sim.set_dynamic_qualification_targets({
        "Forest Mountain Park [Dynamic A]": "future-target",
    })
    assert sim.display_name_for_experience(0) == "Forest Mountain Park [Dynamic A]"



def test_dynamic_candidate_name_remains_visible_if_static_modifiers_are_hidden():
    module = _load_sim_module()
    module.ride_config.SHOW_VARIANT_MODIFIERS = False
    sim = object.__new__(module.Sim)
    sim._visible_indices = (0, 1)
    sim._experiences = (
        "Forest Mountain Park [Dynamic A]",
        "Blackbird [B]",
    )
    sim._park_keys = ("forest", "blackbird")
    sim.set_dynamic_qualification_targets({
        "Forest Mountain Park [Dynamic A]": "relaxed5",
    })
    assert sim.display_name_for_experience(0) == "Forest Mountain Park [Dynamic A] Relaxed"
    assert sim.display_name_for_experience(1) == "Blackbird"

def test_core_uses_audited_profile_target_for_initial_and_live_refresh():
    source = (ROOT / "core" / "poc_core.py").read_text(encoding="utf-8")
    assert source.count('deployment.profile.metadata.get("target")') == 2
    assert source.count('set_dynamic_qualification_targets') >= 2


def test_operator_header_recognises_actual_designer_caption_and_reserves_long_name_width():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert 'normalized in ("ride/flight", "rides/flight")' in source
    assert 'label.setText("Rides:")' in source
    assert 'Forest Mountain Park [Dynamic A] Relaxed' in source
    assert 'required = max(420, min(540, required))' in source
    assert 'get_experience_display_names' in source
    assert 'shrink_for_review = enabled and self._compact_ui' in source


def test_nolimits_launcher_enables_vr_and_preserves_telemetry_and_start_park():
    text = (ROOT / "sim_host_service" / "sims" / "nolimits2.bat").read_text(encoding="utf-8")
    command = next(line for line in text.splitlines() if line.lower().startswith("start "))
    assert '--telemetry --vr --openpark' in command
    assert 'nolimits2app.exe' in command
    assert r'C:\Program Files\NoLimits 2\parks\MDX\MdxLogoCoaster\MdxLogoCoaster.nl2park' in command
