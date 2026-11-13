"""Asynchronous reference-media loading and mapped video playback only.

No change to recording.frames, motion filters, current_s or Twin frame timing.
"""
from __future__ import annotations
from collections import OrderedDict
from pathlib import Path
import time
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox
from .canonical_media import load_media
from .canonical_sync import bind_recording, recording_media_key
from .media_trajectory import MediaCancelled, recording_key


class MediaMapWorker(QThread):
    loaded=pyqtSignal(object)
    failed=pyqtSignal(str)
    progress=pyqtSignal(str)
    def __init__(self,recording,media,token,parent=None):
        super().__init__(parent);self.recording=recording;self.media=media;self.token=token
    def run(self):
        try:
            result=bind_recording(self.recording,media=self.media,
                                 cancel=self.isInterruptionRequested,progress=self.progress.emit)
        except MediaCancelled:
            self.failed.emit('Reference alignment cancelled')
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')
        else:self.loaded.emit(result)


class CanonicalVideoMixin:
    def _init_canonical_media(self):
        self._canonical_binding=None
        self._canonical_worker=None
        self._canonical_workers=set()
        self._canonical_token=0
        self._canonical_cache=OrderedDict()
        self._canonical_source_key=None
        self._media_video_path=None
        self._canonical_decoder_playing=False
        self._canonical_rate=None
        self._canonical_rate_time=-1e20
        self._canonical_available=True
        self._canonical_waiting=False
        self._canonical_first_available_s=None
        self._canonical_unavailable_message=None
        self._canonical_dialog=None
        self._canonical_enabled=self.settings.value('canonical_video_enabled',True,type=bool)

    def _canonical_clear(self):
        self._canonical_token+=1
        if self._canonical_worker:
            self._canonical_worker.requestInterruption()
        self._canonical_binding=None;self._canonical_source_key=None
        self._canonical_available=True;self._canonical_waiting=False
        self._canonical_first_available_s=None
        self._canonical_unavailable_message=None
        self._media_video_path=None
        if hasattr(self,'media_status_label'):self.media_status_label.hide()
        if hasattr(self,'workspace_inspector'):
            self.workspace_inspector.reference_video_label.hide()

    def _media_status(self,message,details=''):
        self.media_status_label.setText(message)
        self.media_status_label.setToolTip(details or message)
        self.media_status_label.show()
        if hasattr(self,'workspace_inspector'):
            label=self.workspace_inspector.reference_video_label
            label.setText(message);label.setToolTip(details or message);label.show()

    def manage_canonical_video(self,*_args):
        from .canonical_dialog import CanonicalVideoDialog
        # This is a deliberate media setup operation, not a change of task.
        self._set_playing(False)
        dialog=CanonicalVideoDialog(self.recording,self)
        dialog.library_changed.connect(self._canonical_library_changed)
        self._canonical_dialog=dialog
        dialog.exec()
        self._canonical_dialog=None
        dialog.deleteLater()

    def _canonical_library_changed(self,key):
        self._canonical_cache.clear();self._canonical_source_key=None
        if self.recording and recording_key(self.recording)==key:
            self._activate_media_for(self.recording,force=True)

    def _canonical_toggled(self,enabled):
        self._canonical_enabled=bool(enabled)
        self.settings.setValue('canonical_video_enabled',self._canonical_enabled)
        self._canonical_source_key=None
        if self.recording:self._activate_media_for(self.recording,force=True)

    def _native_media(self,recording):
        self._canonical_binding=None;self._canonical_available=True;self._canonical_waiting=False
        self._canonical_first_available_s=None
        self._canonical_unavailable_message=None
        self.has_video=bool(recording.video_path)
        self.offset_box.setEnabled(self.has_video and not recording.dynamic_profile)
        self.save_offset_button.setEnabled(self.has_video and not recording.dynamic_profile)
        self.video_offset_s=recording.video_offset_s
        self.sync_label.setText(recording.sync_source)
        path=recording.video_path
        if path:
            if self._media_video_path!=path:
                self.video_backend.load(path,initial_ms=max(0,round((self.current_s+self.video_offset_s)*1000)))
                self._media_video_path=path
            if self.playing and not self._loop_holding:
                self.video_backend.set_speed(self.speed_ratio)
                self.video_backend.play()
        else:
            if self._media_video_path is not None:self.video_backend.unload()
            self._media_video_path=None
        self._update_view_stack()

    def _activate_media_for(self,recording,*,force=False):
        try:
            source=recording_media_key(recording)
            key=recording_key(recording)
        except (OSError,ValueError,KeyError) as exc:
            self._canonical_token += 1
            self._canonical_source_key = None
            if self._canonical_worker:
                self._canonical_worker.requestInterruption()
            self._native_media(recording)
            self._media_status('Reference video unavailable: '+str(exc));return
        if not force and source==self._canonical_source_key:return
        self._canonical_token+=1
        token=self._canonical_token
        self._canonical_source_key=source
        if self._canonical_worker:self._canonical_worker.requestInterruption()
        if not self._canonical_enabled:
            self._native_media(recording);self.media_status_label.hide()
            self.workspace_inspector.reference_video_label.hide()
            return
        try: media=load_media(key)
        except (OSError,ValueError,KeyError) as exc:
            self._native_media(recording)
            self._media_status('Reference video unavailable: '+str(exc));return
        if media is None:
            self._native_media(recording)
            if not self.has_video:
                self._media_status('No ride reference video. Open > Ride reference video... to register an existing pair.')
            else:
                self.media_status_label.hide()
                self.workspace_inspector.reference_video_label.hide()
            return
        cache_key=(source,media.token)
        if cache_key in self._canonical_cache:
            self._canonical_cache.move_to_end(cache_key)
            self._install_binding(self._canonical_cache[cache_key]);return
        self._canonical_binding=None
        self._canonical_waiting=True;self._canonical_available=False
        self._canonical_first_available_s=None
        self._canonical_unavailable_message = (
            'Preparing reference video alignment...\n'
            'Telemetry and motion plots remain available.')
        self.video_backend.pause()
        self._media_status('Preparing ride reference video alignment...',str(media.video_path))
        self._update_view_stack()
        # Do not destroy a running QThread when a new capture replaces it.
        worker=MediaMapWorker(recording,media,token,self)
        self._canonical_worker=worker
        self._canonical_workers.add(worker)
        worker.loaded.connect(lambda result,w=worker,k=cache_key:self._media_map_loaded(w,k,result))
        worker.failed.connect(lambda message,w=worker:self._media_map_failed(w,message))
        worker.progress.connect(lambda message,w=worker:self._media_progress(w,message))
        worker.finished.connect(lambda w=worker:self._media_map_finished(w))
        worker.start()

    def _media_progress(self,worker,message):
        if worker.token==self._canonical_token:self._media_status(message)
    def _media_map_loaded(self,worker,cache_key,binding):
        if worker.token!=self._canonical_token or self.recording is None:return
        self._canonical_cache[cache_key]=binding
        while len(self._canonical_cache)>6:self._canonical_cache.popitem(last=False)
        self._install_binding(binding)
    def _media_map_failed(self,worker,message):
        if worker.token!=self._canonical_token:return
        self._canonical_waiting=False
        self._native_media(self.recording)
        self._media_status('Reference video not used: '+message,
            message+'\nOriginal capture-linked media, if available, remains usable. Motion plots are unchanged.')
    def _media_map_finished(self,worker):
        if worker is self._canonical_worker:self._canonical_worker=None
        self._canonical_workers.discard(worker)
        worker.deleteLater()

    def _install_binding(self,binding):
        self._canonical_binding=binding
        self._canonical_waiting=False;self._canonical_available=True
        self._canonical_first_available_s=binding.first_available_time()
        self._canonical_unavailable_message=None
        self.has_video=True
        self.offset_box.setEnabled(False);self.save_offset_button.setEnabled(False)
        self.offset_box.setToolTip('Shared reference: calibrate in Open > Ride reference video, not against the selected capture clock.')
        self.sync_label.setText('Shared reference video: '+binding.media.key+'; '+binding.source)
        path=binding.media.video_path
        sample=binding.sample(self.current_s)
        self._canonical_decoder_playing=False;self._canonical_rate=None
        self.video_seek_guard.reset()
        if self._media_video_path!=path:
            self.video_backend.load(path,initial_ms=round(sample.video_s*1000) if sample.available else 0)
            self._media_video_path=path
        else:
            self.video_backend.pause()
        self._sync_video(force=True)
        self._update_view_stack()

    def _canonical_clock_ms(self):
        sample=self._canonical_binding.sample(self.current_s)
        return max(0,round(sample.video_s*1000)) if sample.available else self.video_backend.position()

    def _drive_canonical_video(self,force=False):
        binding=self._canonical_binding
        if binding is None:return
        sample=binding.sample(self.current_s)
        available=sample.available
        player=getattr(self.video_backend,'player',None)
        duration=player.duration() if player is not None else 0
        beyond=available and duration>0 and sample.video_s*1000>duration+50
        if beyond:available=False
        changed=available!=self._canonical_available
        self._canonical_available=available
        if not available:
            if self._canonical_decoder_playing:
                self.video_backend.pause();self._canonical_decoder_playing=False
            first = getattr(self, '_canonical_first_available_s', None)
            if (not beyond and first is not None and self.current_s < first - 1e-6):
                remaining = max(0.0, first - self.current_s)
                minutes = int(first // 60)
                clock = f'{minutes:02d}:{first - minutes * 60:04.1f}'
                self._canonical_unavailable_message = (
                    f'Video starts in {remaining:.1f} s.\n'
                    'Telemetry and motion plots are available now.')
                self._media_status(f'Reference video available - begins at {clock}',
                                   binding.source+'\n'+str(binding.media.video_path)+'\n'+'\n'.join(binding.warnings))
            elif beyond:
                self._canonical_unavailable_message = (
                    'Reference video has ended.\n'
                    'Telemetry and motion plots remain available.')
                self._media_status('Reference video has ended', '\n'.join(binding.warnings))
            else:
                self._canonical_unavailable_message = (
                    'Reference video temporarily unavailable - telemetry alignment gap.\n'
                    'Telemetry and motion plots remain available.')
                self._media_status('Reference video temporarily unavailable - telemetry alignment gap',
                                   binding.source+'\n'+'\n'.join(binding.warnings))
            if changed or getattr(self, 'view_mode', 'video') != 'plot':self._update_view_stack()
            return
        self._canonical_unavailable_message=None
        target_ms=max(0,round(sample.video_s*1000))
        now=time.monotonic()
        slope=binding.rate(self.current_s)
        should_play=bool(self.playing and not self._loop_holding and slope>.02)
        rate=max(.1,min(8.,slope*self.speed_ratio))
        if self._canonical_rate is None or (abs(rate-self._canonical_rate)>.015 and now-self._canonical_rate_time>.25):
            self.video_backend.set_speed(rate);self._canonical_rate=rate;self._canonical_rate_time=now
        if force or changed:
            self.video_seek_guard.user_seek(now);self.video_backend.seek(target_ms)
        elif self.video_seek_guard.should_correct_drift(now,self.video_backend.position()-target_ms):
            self.video_backend.seek(target_ms)
        if should_play!=self._canonical_decoder_playing:
            if should_play:
                self.video_seek_guard.resumed(now);self.video_backend.play()
            else:self.video_backend.pause()
            self._canonical_decoder_playing=should_play
        estimated=not binding.media.data['calibration']['verified_by_user']
        self._media_status(f'Reference video: {binding.media.key} | {sample.status}' +
                           (' | offset needs visual check' if estimated else ''),
                           binding.source+'\n'+str(binding.media.video_path)+'\n'+'\n'.join(binding.warnings))
        if changed:self._update_view_stack()

    def _resume_media(self):
        if self._canonical_binding is not None:
            self._drive_canonical_video()
        elif self.has_video:
            self.video_backend.set_speed(self.speed_ratio);self.video_backend.play()
