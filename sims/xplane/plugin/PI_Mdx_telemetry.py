#PI_Mdx_telemetry.py  ver 3
"""
Mdx_Telemetry Plugin
Written by Michael Margolis
Updated for POC to include sim/time/paused in telemetry.

This is the current SOP XPPython3 telemetry plugin with one behavioral addition:
``paused`` is included in the JSON telemetry dictionary so POC can confirm
Run/Pause state from X-Plane rather than relying only on commanded state.
"""

import json
from XPPython3 import xp
from collections import namedtuple
from math import radians
from udp_tx_rx import UdpReceive
from situation_loader import SituationLoader
from accessibility import load_accessibility_settings, set_accessibility

transform_refs = namedtuple('transform_refs', (
    'DR_g_axil', 'DR_g_side', 'DR_g_nrml',
    'DR_Prad', 'DR_Qrad', 'DR_Rrad',
    'DR_theta', 'DR_psi', 'DR_phi',
    'DR_groundspeed'
))

TARGET_PORT = 10022

# Set False after pause-dataref integration has been verified in X-Plane.
XPLANE_PLUGIN_PAUSE_DEBUG = True


class PythonInterface:
    def XPluginStart(self):
        self.Name = "PlatformItf v1.01"
        self.Sig = "Mdx.Python.UdpTelemetry"
        self.Desc = "Sends json 6DoF telemetry + ICAO code over UDP to platform."
        self.controller_addr = []
        self.udp = UdpReceive(10023)
        self.situation_loader = SituationLoader()
        self.settings = load_accessibility_settings()

        self.init_drefs()
        xp.registerFlightLoopCallback(self.InputOutputLoopCallback, 1.0, 0)
        return self.Name, self.Sig, self.Desc

    def XPluginStop(self):
        xp.unregisterFlightLoopCallback(self.InputOutputLoopCallback, 0)
        self.udp.close()

    def XPluginEnable(self):
        return 1

    def XPluginDisable(self):
        pass

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        pass

    def init_drefs(self):
        self.xform_drefs = [
            'sim/flightmodel/forces/g_axil',
            'sim/flightmodel/forces/g_side',
            'sim/flightmodel/forces/g_nrml',
            'sim/flightmodel/position/Prad',
            'sim/flightmodel/position/Qrad',
            'sim/flightmodel/position/Rrad',
            'sim/flightmodel/position/theta',
            'sim/flightmodel/position/psi',
            'sim/flightmodel/position/phi',
            'sim/flightmodel/position/groundspeed'
        ]
        self.OutputDataRef = [xp.findDataRef(ref) for ref in self.xform_drefs]
        self.NumberOfDatarefs = len(self.OutputDataRef)
        self.pauseCmd = xp.findCommand("sim/operation/pause_toggle")
        self.pauseStateDR = xp.findDataRef("sim/time/paused")
        self._last_pause_debug = None
        if XPLANE_PLUGIN_PAUSE_DEBUG:
            xp.log(f"[POC DEBUG] sim/time/paused dataref: {self.pauseStateDR}")
        self.replay_play = xp.findCommand("sim/replay/rep_play_rf")
        self.go_to_replay_begin = xp.findCommand("sim/replay/rep_begin")
        self.acf_icao_ref = xp.findDataRef("sim/aircraft/view/acf_ICAO")
        if self.acf_icao_ref is None:
            xp.log("[WARN] acf_icao_ref dataref not found.")

    def InputOutputLoopCallback(self, elapsedMe, elapsedSim, counter, refcon):
        try:
            msg = self.read_telemetry()
            for addr in self.controller_addr:
                self.udp.send(msg, (addr, TARGET_PORT))
        except Exception as e:
            xp.log(f"[ERROR] Telemetry send failed: {e}")

        while self.udp.available() > 0:
            try:
                addr, payload = self.udp.get()
                msg = payload.split(',')
                cmd = msg[0].strip()

                if cmd == 'InitComs':
                    if addr[0] not in self.controller_addr:
                        self.controller_addr.append(addr[0])
                        xp.log(f"[INFO] Controller added: {addr[0]}")
                elif cmd == 'Run':
                    if xp.getDatai(self.pauseStateDR):
                        xp.log("[INFO] Run command received. Unpausing X-Plane.")
                        xp.commandOnce(self.pauseCmd)
                elif cmd == 'PauseToggle':
                    xp.log("[INFO] Pause toggle command received.")
                    xp.commandOnce(self.pauseCmd)
                elif cmd == 'Pause':
                    is_paused = xp.getDatai(self.pauseStateDR)
                    xp.log(f"[INFO] Pause command received. Current pause state: {is_paused}")
                    if not is_paused:
                        xp.commandOnce(self.pauseCmd)
                elif cmd == 'Play':
                    xp.commandOnce(self.replay_play)
                elif cmd == 'Reset_playback':
                    xp.commandOnce(self.go_to_replay_begin)
                elif cmd == 'Replay' and len(msg) > 1:
                    filepath = msg[1].strip()
                    ret = xp.loadDataFile(xp.DataFile_ReplayMovie, filepath)
                    xp.log(f"[INFO] Loaded Replay: {filepath}, return={ret}")
                elif cmd == 'Situation' and len(msg) > 1:
                    filepath = msg[1].strip()
                    ret = xp.loadDataFile(xp.DataFile_Situation, filepath)
                    xp.log(f"[INFO] Loaded Situation: {filepath}, return={ret}")
                elif cmd == 'FlightMode' and len(msg) > 1:
                    try:
                        mode = int(msg[1].strip())
                        self.situation_loader.load_situation(mode)
                    except Exception as e:
                        xp.log(f"[ERROR] FlightMode invalid: {e}")
                elif cmd == 'AssistLevel' and len(msg) > 1:
                    try:
                        level = int(msg[1].strip())
                        if 0 <= level <= 2:
                            xp.log(f"[INFO] Assist level received: {level}")
                            set_accessibility(['HIGH', 'MODERATE', 'NONE'][level])
                            xp.log(f"[INFO] Set Pilot Assist to: {['HIGH', 'MODERATE', 'NONE'][level]}")
                        else:
                            xp.log(f"[WARN] AssistLevel out of range: {level}")
                    except Exception as e:
                        xp.log(f"[ERROR] AssistLevel invalid: {e}")
            except Exception as e:
                xp.log(f"[ERROR] UDP command handling failed: {e}")

        return 0.025

    def read_telemetry(self):
        try:
            if self.acf_icao_ref is not None:
                icao_buf = [0] * 40
                xp.getDatab(self.acf_icao_ref, icao_buf, 0, 40)
                icao = bytes(icao_buf).decode('utf-8').strip('\x00')
            else:
                raise ValueError("acf_icao_ref is None")
        except Exception:
            icao = "unknown"

        data = [xp.getDataf(ref) for ref in self.OutputDataRef]
        named = transform_refs._make(data)

        paused = int(xp.getDatai(self.pauseStateDR))
        if XPLANE_PLUGIN_PAUSE_DEBUG and paused != self._last_pause_debug:
            xp.log(f"[POC DEBUG] sim/time/paused -> {paused}")
            self._last_pause_debug = paused

        telemetry_dict = {
            "header": "xplane_telemetry",
            "g_axil": -named.DR_Rrad,
            "g_side": -named.DR_Qrad,
            "g_nrml": -named.DR_Prad,
            "Prad": named.DR_g_nrml - 1.0,
            "Qrad": -named.DR_g_side,
            "Rrad": -named.DR_g_axil,
            "phi": radians(named.DR_phi),
            "theta": -radians(named.DR_theta),
            "speed": max(0.0, named.DR_groundspeed),
            "icao": icao,
            "paused": paused,
        }
        return json.dumps(telemetry_dict)
