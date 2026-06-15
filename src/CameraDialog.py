from PySide6.QtCore import QTimer, QSettings, Qt
from PySide6.QtGui import QIcon, QShortcut, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QMessageBox, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QStackedWidget,
                               QWidget)
from PySide6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
from PySide6.QtMultimediaWidgets import QVideoWidget

try:
    from OpenNumismat.Tools.DialogDecorators import storeDlgSizeDecorator
except ModuleNotFoundError:
    from Tools.DialogDecorators import storeDlgSizeDecorator


@storeDlgSizeDecorator
class CameraDialog(QDialog):
    """Camera capture dialog with countdown timer, resolution selection,
    and post-capture preview."""

    TIMER_OPTIONS = [0, 3, 5, 10]  # seconds

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowIcon(QIcon(':/webcam.png'))
        self.setMinimumHeight(100)

        self.image = None
        self.captureSession = QMediaCaptureSession()
        self.camera = None

        # Capture watchdog timer (detects camera blocked by antivirus)
        self.capture_watchdog = QTimer(self)
        self.capture_watchdog.setSingleShot(True)
        self.capture_watchdog.timeout.connect(self._captureTimeout)

        self._buildUI()

        # Countdown timer (1-second tick)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._tickCountdown)
        self.remaining = 0

        # Restore saved camera preference
        settings = QSettings()
        default_camera_id = settings.value('default_camera')
        camera_index = -1
        if default_camera_id:
            camera_index = self.cameraSelector.findData(default_camera_id)
            if camera_index == -1:
                defaultDevice = QMediaDevices.defaultVideoInput()
                camera_index = self.cameraSelector.findData(defaultDevice.id())
        else:
            defaultDevice = QMediaDevices.defaultVideoInput()
            camera_index = self.cameraSelector.findData(defaultDevice.id())

        if camera_index == -1:
            QMessageBox.warning(self.parent(), self.tr("Camera Error"),
                                self.tr("Camera not available"))
        else:
            self.cameraSelector.setCurrentIndex(camera_index)

    # ------------------------------------------------------------------ UI
    def _buildUI(self):
        # -- Page 1: live viewfinder --
        viewfinder_page = QVBoxLayout()

        self.viewfinder = QVideoWidget()

        # Countdown overlay — frameless top-level window positioned over the
        # viewfinder.  This avoids compositing issues with hardware-accelerated
        # video surfaces on some platforms.
        self.countdownOverlay = QLabel()
        self.countdownOverlay.setAlignment(Qt.AlignCenter)
        self.countdownOverlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160);"
            "color: white; font-size: 96px; font-weight: bold;")
        self.countdownOverlay.setFixedSize(200, 200)
        self.countdownOverlay.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.countdownOverlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.countdownOverlay.hide()

        # Camera selector
        self.cameraSelector = QComboBox()
        for cameraDevice in QMediaDevices.videoInputs():
            self.cameraSelector.addItem(cameraDevice.description(),
                                        cameraDevice.id())
        self.cameraSelector.setCurrentIndex(-1)
        self.cameraSelector.currentIndexChanged.connect(self._cameraChanged)

        # Resolution selector
        self.resolutionSelector = QComboBox()
        self.resolutionSelector.setEnabled(False)
        self.resolutionSelector.currentIndexChanged.connect(
            self._resolutionChanged)

        # Action bar
        action_bar = QHBoxLayout()

        self.timerSelector = QComboBox()
        for sec in self.TIMER_OPTIONS:
            label = self.tr("No delay") if sec == 0 else self.tr("%1s").arg(sec)
            self.timerSelector.addItem(label, sec)

        self.shootBtn = QPushButton(self.tr("Shoot"))
        self.shootBtn.clicked.connect(self.shoot)
        self.shootBtn.setEnabled(False)

        self.cancelBtn = QPushButton(self.tr("Cancel"))
        self.cancelBtn.clicked.connect(self.cancelCountdown)
        self.cancelBtn.hide()

        self.shootShortcut = QShortcut(Qt.Key_Space, self)
        self.shootShortcut.activated.connect(self._onSpace)

        action_bar.addWidget(self.timerSelector)
        action_bar.addWidget(self.resolutionSelector)
        action_bar.addStretch()
        action_bar.addWidget(self.shootBtn)
        action_bar.addWidget(self.cancelBtn)

        viewfinder_page.addWidget(self.cameraSelector)
        viewfinder_page.addWidget(self.viewfinder)
        viewfinder_page.addLayout(action_bar)

        # -- Page 2: captured-image preview --
        preview_page = QVBoxLayout()

        self.previewLabel = QLabel()
        self.previewLabel.setAlignment(Qt.AlignCenter)
        self.previewLabel.setMinimumSize(320, 240)

        preview_bar = QHBoxLayout()
        preview_bar.addStretch()

        self.retakeBtn = QPushButton(self.tr("Retake"))
        self.retakeBtn.clicked.connect(self._retake)
        preview_bar.addWidget(self.retakeBtn)

        self.useBtn = QPushButton(self.tr("Use Photo"))
        self.useBtn.clicked.connect(self._confirm)
        preview_bar.addWidget(self.useBtn)

        preview_bar.addStretch()

        preview_page.addWidget(self.previewLabel)
        preview_page.addLayout(preview_bar)

        # -- Stacked container (requires QWidget, not QLayout) --
        vf_widget = QWidget()
        vf_widget.setLayout(viewfinder_page)
        pv_widget = QWidget()
        pv_widget.setLayout(preview_page)

        self.stack = QStackedWidget()
        self.stack.addWidget(vf_widget)   # index 0
        self.stack.addWidget(pv_widget)   # index 1
        self.stack.setCurrentIndex(0)

        layout = QVBoxLayout()
        layout.addWidget(self.stack)
        self.setLayout(layout)

    # ------------------------------------------------------------- Camera
    def _cameraChanged(self, _index):
        # Cancel any running countdown when switching cameras
        if self.countdown_timer.isActive():
            self.cancelCountdown()

        cameraId = self.cameraSelector.currentData()
        for cameraDevice in QMediaDevices.videoInputs():
            if cameraDevice.id() == cameraId:
                self._setCamera(cameraDevice)
                settings = QSettings()
                settings.setValue('default_camera', cameraId)
                break

    def _setCamera(self, cameraDevice):
        self.setWindowTitle(cameraDevice.description())

        # Release previous camera
        if self.camera:
            try:
                self.camera.errorOccurred.disconnect()
            except RuntimeError:
                pass
            self.camera.stop()

        self.camera = QCamera(cameraDevice)
        if self.camera.isFocusModeSupported(QCamera.FocusModeAutoNear):
            self.camera.setFocusMode(QCamera.FocusModeAutoNear)
        self.captureSession.setCamera(self.camera)
        self.camera.errorOccurred.connect(self._handleCameraError)

        self.imageCapture = QImageCapture()
        self.captureSession.setImageCapture(self.imageCapture)
        self.imageCapture.readyForCaptureChanged.connect(self._readyForCapture)
        self.imageCapture.imageCaptured.connect(self._processCapturedImage)
        self.imageCapture.errorOccurred.connect(self._handleCaptureError)

        self.captureSession.setVideoOutput(self.viewfinder)

        # Populate available resolutions
        self._populateResolutions(cameraDevice)

        self.camera.start()

    def _populateResolutions(self, cameraDevice):
        prev_key = None
        if self.resolutionSelector.currentIndex() >= 0:
            prev_key = self.resolutionSelector.currentData()

        self.resolutionSelector.blockSignals(True)
        self.resolutionSelector.clear()

        formats = cameraDevice.videoFormats()
        sizes = []
        seen = set()
        for fmt in formats:
            res = fmt.resolution()
            key = (res.width(), res.height())
            if key not in seen:
                seen.add(key)
                sizes.append(key)

        sizes.sort(key=lambda s: s[0] * s[1])

        for w, h in sizes:
            self.resolutionSelector.addItem(f"{w}\u00d7{h}", (w, h))

        self.resolutionSelector.setEnabled(len(sizes) > 0)

        # Restore previous resolution when available on the new camera
        restored = False
        if prev_key:
            idx = self.resolutionSelector.findData(prev_key)
            if idx >= 0:
                self.resolutionSelector.setCurrentIndex(idx)
                restored = True

        if not restored and self.resolutionSelector.count() > 0:
            self.resolutionSelector.setCurrentIndex(
                self.resolutionSelector.count() - 1)

        self.resolutionSelector.blockSignals(False)

        # Persist the effective resolution
        if self.resolutionSelector.currentIndex() >= 0:
            settings = QSettings()
            settings.setValue('default_resolution',
                              self.resolutionSelector.currentData())

    # --------------------------------------------------------- Resolution
    def _resolutionChanged(self, _index):
        if not self.camera or self.resolutionSelector.currentIndex() < 0:
            return

        w, h = self.resolutionSelector.currentData()
        for fmt in self.camera.cameraDevice().videoFormats():
            res = fmt.resolution()
            if res.width() == w and res.height() == h:
                self.camera.setVideoFormat(fmt)
                self._repositionOverlay()
                break

        settings = QSettings()
        settings.setValue('default_resolution', (w, h))

    # --------------------------------------------------- Countdown & Shoot
    def _onSpace(self):
        """Space bar: start countdown / shoot, or cancel if counting down."""
        if self.countdown_timer.isActive():
            self.cancelCountdown()
        else:
            self.shoot()

    def shoot(self):
        if self.countdown_timer.isActive():
            return
        if not self.imageCapture or not self.imageCapture.isReadyForCapture():
            return

        delay = self.timerSelector.currentData()
        if delay > 0:
            self.remaining = delay
            self.countdown_timer.start()
            self._updateOverlay()
            self.countdownOverlay.show()
            self._repositionOverlay()
            self.cancelBtn.show()
            self.shootBtn.setEnabled(False)
        else:
            self._doCapture()

    def _tickCountdown(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self.countdown_timer.stop()
            self.countdownOverlay.hide()
            self._doCapture()
        else:
            self._updateOverlay()

    def _updateOverlay(self):
        self.countdownOverlay.setText(str(self.remaining))

    def cancelCountdown(self):
        self.countdown_timer.stop()
        self.countdownOverlay.hide()
        self.remaining = 0
        self.cancelBtn.hide()
        self._readyForCapture(
            self.imageCapture.isReadyForCapture() if self.imageCapture else False)

    def _repositionOverlay(self):
        """Center the countdown overlay over the viewfinder."""
        if not self.countdownOverlay.isVisible():
            return
        vf_global = self.viewfinder.mapToGlobal(self.viewfinder.rect().topLeft())
        vw = self.viewfinder.width()
        vh = self.viewfinder.height()
        ow = self.countdownOverlay.width()
        oh = self.countdownOverlay.height()
        self.countdownOverlay.move(
            vf_global.x() + (vw - ow) // 2,
            vf_global.y() + (vh - oh) // 2)

    # ----------------------------------------------------------- Capture
    def _doCapture(self):
        if self.imageCapture and self.imageCapture.isReadyForCapture():
            self.capture_watchdog.start(2500)
            self.imageCapture.capture()

    def _readyForCapture(self, ready):
        self.shootBtn.setEnabled(ready)

    def _processCapturedImage(self, _requestId, img):
        self.capture_watchdog.stop()

        self.image = img

        # Show preview scaled to fit the label
        pixmap = QPixmap.fromImage(img)
        scaled = pixmap.scaled(
            self.previewLabel.size(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.previewLabel.setPixmap(scaled)

        self.stack.setCurrentIndex(1)

    def _retake(self):
        self.image = None
        self.previewLabel.clear()
        self.stack.setCurrentIndex(0)

    def _confirm(self):
        self.accept()

    # ---------------------------------------------------------- Lifecycle
    def done(self, r):
        self.countdown_timer.stop()
        self.countdownOverlay.hide()

        if self.camera:
            self.camera.stop()

        super().done(r)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._repositionOverlay()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._repositionOverlay()

    # ----------------------------------------------------- Error Handling
    def _handleCameraError(self):
        if self.camera and self.camera.error() != QCamera.NoError:
            error_msg = self.camera.errorString()
            self.countdown_timer.stop()
            self.countdownOverlay.hide()
            self.cancelBtn.hide()

            # Release camera resources
            self.camera.stop()
            self.shootBtn.setEnabled(False)

            QMessageBox.warning(self, self.tr("Camera Error"), error_msg)

            # Reset selector so the user can retry or pick another device
            self.cameraSelector.setCurrentIndex(-1)

    def _handleCaptureError(self, _id, _error, errorString):
        self.capture_watchdog.stop()
        self.countdown_timer.stop()
        self.countdownOverlay.hide()
        self.cancelBtn.hide()
        self.shootBtn.setEnabled(False)
        QMessageBox.warning(self, self.tr("Image Capture Error"), errorString)

    def _captureTimeout(self):
        """Fires when no image arrives within 2.5 s — typically caused by
        antivirus software blocking camera access."""
        QMessageBox.warning(self, self.tr("Camera Error"),
                            self.tr("Camera not available or disabled "
                                    "by antivirus"))
