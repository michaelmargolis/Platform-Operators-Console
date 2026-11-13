"""Built-in help for the task workspace; no Qt or processing dependencies.

Topics follow the recovered Qt prototype's six-tab help. Descriptions refer
only to the implemented player; reference-video uncertainty is explicitly labelled.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class HelpTopic:
    key: str
    title: str
    html: str


WORKFLOW_QUICK_HELP = {
    'review': {
        'title': 'Review workflow',
        'purpose': 'Understand what the selected ride and candidate are doing before you change anything.',
        'steps': (
            'Choose the platform, ride and candidate you want to inspect.',
            'Use Video, Plot or Overlay and select the trace that best explains the motion.',
            'Play or seek through the ride; use markers, events, motion and muscle readouts to investigate notable sections.',
            'Use Candidate A/B when you need to compare alternatives at the same replay time.',
            'Save a review or notes if useful; move to Tune to experiment or Validate to check calculated limits and provenance.',
        ),
    },
    'tune': {
        'title': 'Tune workflow',
        'purpose': 'Experiment offline with motion settings and assess the result without changing the live POC configuration.',
        'steps': (
            'Start from the raw/static candidate you want to change; dynamic-profile studies are read-only.',
            'Adjust gains, neutral position, angular cue or translational washout deliberately, preferably one change at a time.',
            'Apply/reanalyse so the complete recording is recalculated through the existing POC processing path.',
            'Use Plot or Overlay, baseline/A-B comparison and the inspector to judge the effect across the ride.',
            'Save an experiment, review or new named variant when the result is worth keeping; this is not physical-platform approval.',
        ),
    },
    'validate': {
        'title': 'Validate workflow',
        'purpose': 'Check calculated feasibility, synchronisation and provenance before deciding whether a candidate should progress.',
        'steps': (
            'Select the candidate and recording you want to check.',
            'Review requested muscle range, excess and clipping results; inspect the largest anomalies when needed.',
            'Use the relevant plot or overlay to understand where the calculated limit occurs.',
            'Check reference-video alignment, dynamic-profile status and source/provenance information.',
            'Record or save the review outcome. Calculated validation does not establish comfort, tension-only feasibility or physical safety.',
        ),
    },
}


def workflow_quick_help(key):
    """Return concise workflow guidance without importing Qt."""
    return WORKFLOW_QUICK_HELP.get(str(key).lower())


HELP_TOPICS = (
    HelpTopic('workflows', 'Workflows', '''
<h2>One player, three tasks</h2>
<p><b>Review</b> inspects motion, muscle lengths, events, candidate A/B differences
and trial notes. <b>Tune</b> exposes base gains, neutral height, angular cue and
experimental translational washout. <b>Validate</b> shows calculated kinematic
limits, synchronisation information and provenance.</p>
<p>The <b>Task</b> selector opens the relevant inspector section and chooses a
related plot. Changing tasks preserves the loaded recording, active candidate,
playhead, loop range and Video / Plot / Overlay choice. The inspector scrolls
independently; expand only the sections you need.</p>
<h3>Menus, not extra toolbars</h3>
<p><b>Open</b>: a capture, static optimiser candidates, dynamic profiles, or a
saved review. <b>File</b>: save reviews/variants and player settings.
<b>View</b>: inspector and layout controls. <b>Advanced</b>: detailed source
controls and the original diagnostic docks. <b>Twin</b>: Visualizer status,
destination and settings.</p>
<p>Use the Platform, Ride and Candidate selectors for the current review.
An A/B review stays within its selected platform. Open another review to change
that pairing; an archived candidate is not silently reinterpreted for another platform.</p>
<p>Hover <b>Review</b>, <b>Tune</b> or <b>Validate</b> for a task-specific hint.
Right-click any of those workflow buttons for a short purpose-and-process guide.
<b>Help &gt; Workspace help</b>, the information icon and <b>F1</b> open this
full tabbed window. Help does not reset, pause, seek or reanalyse the ride.</p>
'''),
    HelpTopic('playback', 'Markers and playback', '''
<h2>Keep the ride in one main view</h2>
<p><b>Video</b> shows the associated recording. <b>Plot</b> uses the main area for
the selected trace. <b>Overlay</b> places the selected plot in a translucent grey
band over the lower video area. There is no split Video + Plot layout. The Plot
selector applies to both Plot and Overlay. Coloured line swatches identify each trace; dashed lines identify the comparison or source stage as labelled. Without video the player shows plot
information instead.</p>
<h3>Inspect events and loop a section</h3>
<p>Orange kinematic bars show <b>duration</b>. Hover for details and click to seek
to the event peak. The Markers selector switches between calculated kinematic
anomalies and the captured ride events that can be aligned.</p>
<p>Drag the <b>red A</b> and <b>gold B</b> range handles independently of the
playhead. <b>Mark</b> centres the selected duration on the playhead and selects
Loop range. <b>Reset range</b> restores the full interval.</p>
<p><b>Full ride</b> ignores A/B; <b>Play range</b> stops at B; <b>Loop range</b>
returns to A after the selected <b>Delay</b>, holding the final frame and Twin
pose first. The arrow buttons step one recorded sample; -5s/+5s jump five seconds.
Speed changes replay speed, not the stored analysis.</p>
<p>The <b>Panels</b> menu and <b>Advanced &gt; Panels</b> expose detailed docks.
<b>View &gt; Restore task workspace</b> returns to one inspector.</p>
<p>Playback-range A/B handles and candidate A/B slots are different controls:
the former select a time interval; the latter select alternative settings.</p>
'''),
    HelpTopic('gains', 'Gains versus profiles', '''
<h2>Base gains and dynamic multipliers are different</h2>
<p>The six Tune sliders are <b>base motion gains</b>, relative to the saved POC
setting (100% means unchanged). They are not dynamic-profile control points.
The existing cue, gain and washout processing produces a base transform;
a dynamic profile then applies six time-varying multipliers before kinematics.</p>
<p>Use <b>Dynamic multipliers</b> in the Plot selector to inspect that profile.
A flat multiplier does not mean washout is disabled. The final motion also
depends on the input cue, filtering, neutral position and platform limits.</p>
<h3>Compare and save deliberately</h3>
<p><b>Open &gt; Review optimiser candidates</b> offers all available static
results for the chosen ride/platform. Select one or any two, including two
strict or two relaxed candidates. Each working copy retains its own manual
changes. Switch the active Candidate to compare at the same replay time.</p>
<p>Optimiser results are starting points, not prescribed experiences. After
modifying a static candidate, save it with a <b>new unique name</b>; its original
strict/relaxed description no longer establishes qualification. Save a review
for unfinished work, or explicitly save a new candidate variant for later POC
review. Neither action makes it preferred automatically.</p>
<p><b>Dynamic-profile review is read-only in this release.</b> Compare a profile
with another profile, its Static 100% baseline or its stored static fallback.
Saving a dynamic review preserves the selection and playback state, not an
edited profile or a live POC variant.</p>
<p>Different captures can reuse a canonical ride video through trajectory
mapping. Candidate A/B still compares the same selected source recording;
this does not create a two-independent-capture motion comparison. The plot and
loop clock remain the selected recording's time.</p>
'''),
    HelpTopic('video', 'Reference video', ''' 
<h2>One reference video per ride</h2>
<p>Use <b>Open &gt; Ride reference video...</b>. Select the configured ride,
then the <b>original telemetry CSV recorded with that video</b> and the video.
Confirm the pair and import it. Existing files are copied, never moved or deleted.
They are kept under <code>tools/sim_player/canonical_rides/&lt;park_key&gt;/</code>.
No extra copy is needed per platform, candidate or new telemetry repetition.</p>
<h3>Calibrate the original pair once</h3>
<p>The existing saved player offset is imported automatically. Otherwise the
initial offset may only be an OBS estimate. It is the video timestamp at the
original CSV's elapsed zero: positive means video started earlier. Check a
recognisable event before relying on the alignment. The <b>Calibration</b> tab
accepts reference-CSV/video timestamp pairs. One anchor is a constant offset;
multiple checked anchors can compensate for video-clock drift. Event choices
locate the telemetry event; enter the corresponding visible video timestamp.
Calibration edits affect the media reference only, not motion settings.</p>
<h3>How another capture uses that video</h3>
<p>B+C matches recorded XYZ and quaternion orientation to the canonical
trajectory, anchored by first sustained movement. The mapping is calculated
on a worker thread and cached. Playback, seeking and loops use the selected
capture's existing timeline; only the presented video time/rate is mapped.
The status line shows the alignment state. The video may play slightly faster
or slower where the two runs progressed at different speeds.</p>
<p>Saved dynamic studies use their archived B+C progress and checksum-bound
reference trajectory. This works without copying the original raw session,
but is <b>not a new verification of the absent target raw telemetry</b>.
Stationary holds are marked <b>timing estimates</b>, not precise spatial fixes.</p>
<p>Known park/track/seat incompatibilities or inadequate route coverage report
<b>New video capture required</b> (or select the correct pair). A missing/gapped
match is not silently interpolated into apparent synchronisation: video is
unavailable there while plots and telemetry remain usable. Video end-of-file
is handled separately. Park hashes are unavailable in some legacy captures;
then route checks and your explicit original-pair confirmation are essential.</p>
<p><b>View &gt; Use ride reference video</b> switches shared-media reuse on/off.
Turning it off returns to the recording's own video if it has one. Ordinary
capture offset controls remain in Advanced; canonical calibration is managed
in the reference dialog to avoid applying the selected capture's clock offset
a second time. Existing candidate A/B and motion computations are unchanged.</p>
'''),
    HelpTopic('washout', 'Washout', '''
<h2>Time constants are in seconds</h2>
<p><b>Tune &gt; Washout</b> shows the effective Surge and Sway enable flags and
classical washout time constants. Adjust the numeric values, then choose
<b>Reanalyse with these settings</b>. There are no washout percentage sliders.
The current player edits the two axes independently; linked editing was a
prototype idea and is not present in this build.</p>
<p>Editing requires a non-dynamic capture that can be reprocessed and a classical
washout settings snapshot. The player replays the whole capture through the
existing POC processing so filter history is consistent. It does not write to
<code>telemetry_config.ini</code> or change deployed platform settings.</p>
<p>A saved dynamic study contains already-filtered data. Its washout controls
remain read-only: changing a filter requires raw-data reconstruction and
regeneration/revalidation of the dynamic profile. Old qualification does not
carry over to altered filters.</p>
<p>The NL2-versus-processed plots contain cue/gain/filter effects together;
they are <b>not a filter-only response measurement</b>. Dynamic gain multipliers
are a separate signal. Compare with a static baseline to help distinguish them.</p>
<p>The player helps inspect timing and kinematic consequences. Useful washout
settings ultimately need <b>feedback on the physical platform</b>; numerical
feasibility does not establish the subjective experience.</p>
'''),
    HelpTopic('limits', 'Limits and safety', '''
<h2>Muscle bars and requested peaks</h2>
<p>Review's muscle bars use the same display as Advanced &gt; Muscle lengths.
The bar represents the <b>final permitted length</b>. Blue and gold vertical
markers show the minimum and maximum <b>requested</b> lengths visited in this
replay. A peak marker is <b>red outside the valid range</b>, even after the
current demand has returned within range. Grey guides mark valid limits and
50 mm intervals. Warning gutters compress excess distance; hover for exact mm.</p>
<p>Peaks persist through backward seeks, pauses and automatic A/B loops. A new
capture, changed processing or deliberate replay restart clears their history.
Strict candidates need not show red markers if no requested limit was exceeded.</p>
<h2>What Validate does - and does not - establish</h2>
<p>Validate displays calculated requested muscle-length excess, final clipping,
workspace intervention, output-step and available clearance/synchronisation
information. A source-compatibility or output-parity check does not transfer an
old profile's qualification to different processing code or geometry.</p>
<p>These are offline diagnostics, not measured pneumatic response, comfort or
force/tension feasibility. Strict/relaxed optimisation targets do not enlarge
physical actuator limits. Dynamic profiles remain research review items, not
live POC exports in this build.</p>
<h3>Output and saved data</h3>
<p>The player does not connect to live NL2, SHS or Festo. Optional <b>Twin</b>
output sends replay poses to the Visualizer over UDP with zero command pressures.
The Twin menu shows whether it is enabled, holding, sending or reporting an error;
its settings use the existing output configuration.</p>
<p>Capture and optimiser source files are not edited by playback or help.
Static variant saving is explicit; changing washout must not silently modify
the live global configuration. Trial notes stay in the current recording session
until <b>Save notes as JSON</b> is used; save them before changing recordings.</p>
<h3>Features still pending</h3>
<p>Interactive dynamic-profile editing and live profile promotion are not enabled.
Canonical reference-video remapping is available, but uncovered or low-information
intervals remain explicitly unavailable/estimated rather than being invented.
Opening Help does not run analysis, create files or enable any output.</p>
'''),
)


def help_topics():
    """Return the immutable topics for GUI and documentation/tests."""
    return HELP_TOPICS
