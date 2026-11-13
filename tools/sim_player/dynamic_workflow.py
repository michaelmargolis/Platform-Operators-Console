"""Profile-aware workflow mixed into the existing player; no live promotion."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import QThread,pyqtSignal,Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QDialog,QFileDialog,QMessageBox,QWidget,QHBoxLayout,
    QLabel,QComboBox,QPushButton)
from .dynamic_review import (ROOT,MODE_LABELS,ReviewSpec,build_session,save_review,
                             read_review,profile_choice,ReviewCancelled)
from .dynamic_browser import DynamicProfileBrowserDialog
from .dynamic_panel import GainPlot,DynamicProfilePanel
from sims.nolimits2.variant_store import VariantStore


class DynamicReviewWorker(QThread):
    loaded=pyqtSignal(object)
    failed=pyqtSignal(str)
    progress=pyqtSignal(str)
    def __init__(self,specs,parent=None):
        super().__init__(parent);self.specs=tuple(specs)
    def run(self):
        try:
            result=build_session(self.specs,cancel=self.isInterruptionRequested,progress=self.progress.emit)
        except ReviewCancelled:
            self.failed.emit('Profile loading cancelled; previous view retained.')
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')
        else:self.loaded.emit(result)


class DynamicProfileWorkflowMixin:
    def _init_dynamic_state(self):
        self._dynamic_session=None
        self._dynamic_pending=False
        self._dynamic_resume=None

    def _create_profile_strip(self,layout):
        self.profile_strip=QWidget();row=QHBoxLayout(self.profile_strip);row.setContentsMargins(0,0,0,0)
        row.addWidget(QLabel('Dynamic review'))
        self.profile_slot=QComboBox();self.profile_slot.setMinimumWidth(240)
        self.profile_slot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_slot.setMinimumContentsLength(22)
        self.profile_slot.currentIndexChanged.connect(self._dynamic_switch);row.addWidget(self.profile_slot,1)
        self.profile_source=QLabel();row.addWidget(self.profile_source)
        self.profile_graph_button=QPushButton('Gains / video');self.profile_graph_button.clicked.connect(self._toggle_profile_graph);row.addWidget(self.profile_graph_button)
        self.profile_save_button=QPushButton('Save review...');self.profile_save_button.clicked.connect(self.save_dynamic_review);row.addWidget(self.profile_save_button)
        self.profile_cancel_button=QPushButton('Cancel load');self.profile_cancel_button.clicked.connect(self._cancel_dynamic_load);row.addWidget(self.profile_cancel_button)
        layout.addWidget(self.profile_strip);self.profile_strip.hide()

    def _create_profile_canvas(self):
        self.profile_plot=GainPlot();self.profile_plot.seek_requested.connect(self.seek_seconds)
        self.video_stack.addWidget(self.profile_plot)

    def _create_profile_dock(self):
        self.profile_panel=DynamicProfilePanel();self.profile_panel.seek_requested.connect(self.seek_seconds)
        dock=self._add_dock('dynamic_profile','Dynamic profile - gains and sync',self.profile_panel)
        self.tabifyDockWidget(self.panels['muscles'],dock);dock.hide()

    def _create_profile_menu(self,file_menu):
        action=QAction('Review dynamic motion profiles...',self)
        action.triggered.connect(self.pick_dynamic_profiles);file_menu.addAction(action)
        action=QAction('Open saved dynamic review...',self)
        action.triggered.connect(self.open_dynamic_review);file_menu.addAction(action)
        self.save_dynamic_action=QAction('Save dynamic review...',self)
        self.save_dynamic_action.triggered.connect(self.save_dynamic_review)
        self.save_dynamic_action.setEnabled(False);file_menu.addAction(self.save_dynamic_action)

    def pick_dynamic_profiles(self,checked=False,directory=None):
        if self._review_busy() or not self._confirm_replace_review():return
        directory=directory or self.settings.value('dynamic_profiles_directory',str(ROOT/'studies/nl2/dynamic_profiles'))
        dialog=DynamicProfileBrowserDialog(directory,self)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.specs:
            self.settings.setValue('dynamic_profiles_directory',str(dialog.directory))
            self._start_dynamic_review(dialog.specs)

    def _open_initial_path(self,path):
        path=Path(path)
        if path.is_dir():
            self.pick_dynamic_profiles(directory=path)
        elif path.name=='profile.json':
            try:
                choice=profile_choice(path)
                self._start_dynamic_review((ReviewSpec(choice.path,choice.captures[0]),
                                           ReviewSpec(choice.path,choice.captures[0],'unity')))
            except (ValueError,OSError,KeyError) as exc:
                QMessageBox.warning(self,'Cannot open dynamic profile',str(exc))
        elif path.suffix.lower()=='.json':
            self._open_dynamic_review_path(path)
        else:self.open_capture(path)

    def _start_dynamic_review(self,specs,resume=None):
        if self._review_busy():return
        self._set_playing(False)
        self.loading=True;self._dynamic_pending=True;self._dynamic_resume=resume
        self._set_analysis_busy();self._update_dynamic_ui()
        self.worker=DynamicReviewWorker(specs,self)
        self.worker.loaded.connect(self._dynamic_load_success)
        self.worker.failed.connect(self._dynamic_load_failure)
        self.worker.progress.connect(self._dynamic_progress)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _dynamic_progress(self,message):self.statusBar().showMessage(message)

    def _dynamic_load_success(self,session):
        self._dynamic_pending=False;self.loading=False
        self._candidate_session=None;self._candidate_switch_previous=None;self._review_resume_target=None
        self._dynamic_session=session;self._mode_cache.clear()
        self.variant_store=VariantStore.load_readonly(session.recording.platform_key)
        self.video_seek_guard.reset();self.scrub_seek_timer.stop()
        self._load_success(session.recording)
        self._update_candidate_ui();self._refresh_dynamic_view()
        if self._dynamic_resume is not None:
            active,seconds,loop=self._dynamic_resume;self._dynamic_resume=None
            session.active=active
            self._apply_reanalysis(session.recording);self._refresh_dynamic_view()
            if isinstance(loop,dict):
                try:
                    self.loop_range.set_start(float(loop.get('start',0.)))
                    self.loop_range.set_end(float(loop.get('end',session.recording.duration)))
                    self.loop_range.set_mode(loop.get('mode','full'))
                    self.loop_range.set_delay(float(loop.get('loop_delay_s',1.)))
                    self._refresh_range_controls()
                except (ValueError,TypeError):
                    self.loop_range.reset(session.recording.duration)
            self.seek_seconds(seconds)
        self.statusBar().showMessage(session.recording.diagnostics)

    def _dynamic_load_failure(self,message):
        self.loading=False;self._dynamic_pending=False;self._dynamic_resume=None
        self._update_dynamic_ui()
        self.statusBar().showMessage(message)
        if not message.startswith('Profile loading cancelled'):
            QMessageBox.warning(self,'Dynamic profile could not be opened',message)

    def _cancel_dynamic_load(self):
        if self._dynamic_pending and self.worker is not None:
            self.worker.requestInterruption();self.profile_cancel_button.setEnabled(False)

    def _clear_dynamic_review(self):
        self._dynamic_session=None;self._dynamic_pending=False;self._dynamic_resume=None
        if hasattr(self,'profile_strip'):
            self.profile_strip.hide();self.panels['dynamic_profile'].hide()
            self.profile_plot.set_recording(None);self.profile_panel.set_recording(None)
            self.tuning_panel.setEnabled(True)
            self.raw_axes_action.setText('Show original NL2 CSV transform instead of processed')
            self.save_dynamic_action.setEnabled(False)

    def _dynamic_readonly(self):
        if getattr(self,'_dynamic_session',None) is not None:
            self.statusBar().showMessage('Dynamic profile review is read-only. Use Open capture / Review candidates for static tuning. Live profile promotion is not enabled.',7000)
            return True
        return False

    def _update_dynamic_ui(self):
        if not hasattr(self,'profile_strip'):return
        session=self._dynamic_session;busy=self._review_busy()
        self.profile_strip.setVisible(session is not None or self._dynamic_pending)
        self.profile_cancel_button.setVisible(self._dynamic_pending)
        self.profile_cancel_button.setEnabled(self._dynamic_pending)
        self.profile_slot.blockSignals(True);self.profile_slot.clear()
        if session:
            for i,rec in enumerate(session.recordings):
                d=rec.dynamic_profile
                self.profile_slot.addItem(f'{"AB"[i]} - {d["label"]}',i)
            self.profile_slot.setCurrentIndex(session.active)
            differences = sorted({name for rec in session.recordings
                                  for name in rec.dynamic_profile.get('processing_source_mismatches', ())})
            self.profile_source.setText(('SOURCE DIFF - VERIFIED OUTPUTS | review only'
                                         if differences else session.source+' | read-only'))
            self.profile_source.setToolTip(('Installed source differs from archived profile: '
                                            + ', '.join(differences)
                                            + '. Saved dynamic output parity passed; original qualification is not valid for this checkout.'
                                            if differences else 'Original study and current checkout match.'))
        else:self.profile_source.setText('Preparing full capture...')
        self.profile_slot.blockSignals(False)
        self.profile_slot.setEnabled(bool(session and len(session.recordings)>1 and not busy))
        self.profile_save_button.setEnabled(session is not None and not busy)
        self.profile_graph_button.setEnabled(session is not None and self.has_video and not busy)
        if hasattr(self,'save_dynamic_action'):self.save_dynamic_action.setEnabled(session is not None and not busy)
        if session:
            self.tuning_panel.setEnabled(False)
            self.cue_box.setEnabled(False);self.variant_box.setEnabled(False);self.platform_box.setEnabled(False)
            self.save_variant_action.setEnabled(False)
            # Video offset calibration belongs to the original capture, not a
            # generated diagnostic CSV. Raw-session alignment is read-only here.
            self.offset_box.setEnabled(False);self.save_offset_button.setEnabled(False)
            self.raw_axes_action.setText('Show base post-washout transform instead of profile-scaled request')
        elif not self._dynamic_pending:
            self.tuning_panel.setEnabled(True)

    def _refresh_dynamic_view(self):
        session=self._dynamic_session
        if session is None:return
        rec=session.recording
        other=session.recordings[1-session.active] if len(session.recordings)==2 else None
        self._baseline_recording=other or rec
        self.profile_plot.set_recording(rec,other);self.profile_panel.set_recording(rec,other)
        self._update_tuning_feedback()
        # New task workspace keeps the full dynamic dock available in View >
        # Panels, but does not force it open over the contextual inspector.
        if not hasattr(self, 'workspace_inspector'):
            self.panels['dynamic_profile'].show();self.panels['dynamic_profile'].raise_()
        self.ride_label.setText(rec.ride.upper()+' / DYNAMIC PROFILE REVIEW')
        self.setWindowTitle('MDX sim_player - '+rec.dynamic_profile['label'])
        if hasattr(self, '_refresh_workspace_selectors'):
            self._refresh_workspace_selectors()
            self._refresh_workspace_inspector()
            self._update_view_stack()
        elif not self.has_video:
            self.video_stack.setCurrentWidget(self.profile_plot)
        self._update_dynamic_ui();self._display_current()

    def _dynamic_switch(self,*args):
        session=self._dynamic_session;index=self.profile_slot.currentData()
        if session is None or index is None:return
        if index==session.active or self._review_busy():
            self._update_dynamic_ui();return
        was_playing=self.playing;self._set_playing(False)
        session.active=int(index)
        self._apply_reanalysis(session.recording)
        self._refresh_dynamic_view()
        if was_playing:self._set_playing(True)

    def _display_dynamic_frame(self,frame):
        if not getattr(self,'_dynamic_session',None) or not getattr(self.recording,'dynamic_profile',None):return
        self.profile_plot.set_current(self.current_s);self.profile_panel.show_frame(frame)
        original=self.raw_axes_action.isChecked()
        self.axis_bars.axis_context='BASE POST-WASHOUT' if original else 'PROFILE REQUEST'
        self.panels['axes'].setWindowTitle('Transform - base post-washout' if original else 'Transform - profile-scaled request')
        self.axis_bars.update()

    def _toggle_profile_graph(self):
        if self._dynamic_session is None:return
        if hasattr(self, '_view_mode_selected'):
            if self.video_stack.currentWidget() == self.profile_plot:
                self._view_mode_selected('video' if self.has_video else 'plot')
            else:
                self.video_stack.setCurrentWidget(self.profile_plot)
        elif self.has_video and self.video_stack.currentWidget() == self.profile_plot:
            self.video_stack.setCurrentWidget(self.video_screen)
        else:
            self.video_stack.setCurrentWidget(self.profile_plot)

    def save_dynamic_review(self):
        session=self._dynamic_session
        if session is None or self._review_busy():return
        folder=ROOT/'studies/nl2/profile_reviews';folder.mkdir(parents=True,exist_ok=True)
        name=session.recording.dynamic_profile['profile']['identity']['park']+'_review_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json'
        path,_=QFileDialog.getSaveFileName(self,'Save offline dynamic review',str(folder/name),'Dynamic review (*.json)')
        if not path:return
        if not path.lower().endswith('.json'):path+='.json'
        try:
            save_review(path,session,self.current_s,asdict(self.loop_range))
            self.statusBar().showMessage('Saved review only; no variant/profile altered: '+path,10000)
        except (OSError,ValueError) as exc:QMessageBox.warning(self,'Review not saved',str(exc))

    def open_dynamic_review(self):
        if self._review_busy() or not self._confirm_replace_review():return
        path,_=QFileDialog.getOpenFileName(self,'Open saved dynamic review',str(ROOT/'studies/nl2/profile_reviews'),'Dynamic review (*.json)')
        if path:self._open_dynamic_review_path(path)

    def _open_dynamic_review_path(self,path):
        try:
            specs,active,seconds,loop=read_review(path)
            self._start_dynamic_review(specs,(active,seconds,loop))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            QMessageBox.warning(self,'Cannot reopen dynamic review',str(exc))
