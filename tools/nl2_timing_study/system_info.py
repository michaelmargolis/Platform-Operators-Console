# system_info.py ver 1
"""Host and NL2 VR evidence.  No simulator or hardware dependencies."""
from __future__ import annotations
import platform
import sys

VR_HEADSET_ON = 0x08  # NoLimits 2 telemetry state, bit 3


def vr_summary(on, off):
    """Count observed NL2 state-flag samples; unknown is NOT VR off."""
    on, off = int(on), int(off)
    if on < 0 or off < 0:
        raise ValueError("VR sample counts must not be negative")
    state = ('mixed' if on and off else 'on' if on else 'off' if off else 'unknown')
    return dict(state=state, headset_on_samples=on, headset_off_samples=off,
                total_samples=on+off, state_mask_hex='0x08')


def vr_from_states(values):
    on = off = 0
    for value in values:
        try:
            state = int(value)
        except (TypeError, ValueError):
            continue
        if state & VR_HEADSET_ON:
            on += 1
        else:
            off += 1
    return vr_summary(on, off)


def _edition_name(edition):
    if not edition:
        return ''
    e = str(edition).strip()
    exact = {'Professional': 'Pro', 'ProfessionalN':'Pro N', 'ProfessionalEducation':'Pro Education',
             'Core':'Home', 'CoreSingleLanguage':'Home Single Language',
             'Enterprise':'Enterprise', 'EnterpriseS':'Enterprise LTSC',
             'Education':'Education', 'ServerStandard':'Server Standard'}
    return exact.get(e, e)


def os_details():
    """Use the Windows NT build, NOT platform.platform()'s Windows-10 label.

    Microsoft keeps the NT major version 10 for Windows 11.  Where possible
    the installed edition is read from the OS registry, not guessed from the
    Win32 release string. The original platform string remains available.
    """
    raw = platform.platform()
    if sys.platform != 'win32':
        return dict(os=raw, os_raw=raw)
    build = None
    try:
        build = int(sys.getwindowsversion().build)
    except (AttributeError, OSError, ValueError, TypeError):
        pass
    edition = ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as key:
            # Registry build is authoritative if the Python runtime returns
            # a compatibility/manifested version rather than the actual build.
            try:
                registry_build = int(winreg.QueryValueEx(key, 'CurrentBuildNumber')[0])
                if registry_build > 0:
                    build = registry_build
            except (OSError, ValueError, TypeError):
                pass
            try: edition = _edition_name(winreg.QueryValueEx(key, 'EditionID')[0])
            except (OSError, ValueError, TypeError): pass
    except (ImportError, OSError):
        pass
    if build is None:
        try:
            version = platform.win32_ver()[1]
            build = int(version.rsplit('.', 1)[-1])
        except (OSError, ValueError, TypeError, IndexError):
            pass
    name = 'Windows 11' if build is not None and build >= 22000 else 'Windows 10'
    if build is None:
        return dict(os=raw, os_raw=raw)
    display = name + ((' ' + edition) if edition else '') + f' (build {build})'
    return dict(os=display, os_raw=raw, os_build=build, os_edition=edition)
