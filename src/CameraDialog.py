from PySide6.QtCore import QTimer, QSettings, Qt, QSize
from PySide6.QtGui import QIcon, QShortcut, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QMessageBox, QVBoxLayout,
                               QHBoxLayout, QFormLayout, QPushButton,
                               QStackedWidget, QLabel, QWidget)
from PySide6.QtMultimedia import (QCamera, QImageCapture, QMediaCaptureSession,
                                  QMediaDevices, QCameraFormat)
from PySide6.QtMultimediaWidgets import QVideoWidget

try:
    from OpenNumismat.Tools.DialogDecorators import storeDlgSizeDecorator
except ModuleNotFoundError:
    from Tools.DialogDecorators import storeDlgSizeDecorator


@storeDlgSizeDecorator
class CameraDialog(QDialog):

    COUNTDOWN_OPTIONS = (0, 3, 5, 10)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowIcon(QIcon(':/webcam.png'))
        self.setMinimumHeight(100)

        self.image = None
        self._pendingImage = None
        self._previewPixmap = None
        self.captureSession = QMediaCaptureSession()
        self.camera = None
        self.imageCapture = None
        self._activeCameraId = None
        self._remaining = 0

        # Watchdog: a camera silently blocked (e.g. by antivirus) never
        # delivers the captured frame, so guard the capture with a timer.
        self.first_capture_timer = QTimer(self)
        self.first_capture_timer.setSingleShot(True)
        self.first_capture_timer.timeout.connect(self.firstCaptureTimeout)

        # Countdown ticking once per second before the actual shot.
        self.countdownTimer = QTimer(self)
        self.countdownTimer.setInterval(1000)
        self.countdownTimer.timeout.connect(self._onCountdownTick)

        # Detect cameras being plugged in / unplugged while the dialog is open.
        self._mediaDevices = QMediaDevices(self)
        self._mediaDevices.videoInputsChanged.connect(self._onVideoInputsChanged)

        self._buildUi()
        self._populateCameras()
        self._selectInitialCamera()

    # ------------------------------------------------------------------ UI

    def _buildUi(self):
        # --- capture page controls ---
        self.cameraSelector = QComboBox()
        self.cameraSelector.currentIndexChanged.connect(self.cameraChanged)

        self.resolutionSelector = QComboBox()
        self.resolutionSelector.currentIndexChanged.connect(self._onResolutionChanged)

        self.countdownSelector = QComboBox()
        self.countdownSelector.addItem(self.tr("Off"), 0)
        self.countdownSelector.addItem(self.tr("3 s"), 3)
        self.countdownSelector.addItem(self.tr("5 s"), 5)
        self.countdownSelector.addItem(self.tr("10 s"), 10)
        settings = QSettings()
        saved_countdown = settings.value('camera_countdown', 0, type=int)
        countdown_index = self.countdownSelector.findData(saved_countdown)
        if countdown_index >= 0:
            self.countdownSelector.setCurrentIndex(countdown_index)
        self.countdownSelector.currentIndexChanged.connect(self._onCountdownChanged)

        self.controls = QWidget()
        form = QFormLayout(self.controls)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.tr("Camera:"), self.cameraSelector)
        form.addRow(self.tr("Resolution:"), self.resolutionSelector)
        form.addRow(self.tr("Countdown:"), self.countdownSelector)

        self.viewfinder = QVideoWidget()

        self.countdownLabel = QLabel()
        self.countdownLabel.setAlignment(Qt.AlignCenter)
        self.countdownLabel.setStyleSheet("font-size: 36px; font-weight: bold;")
        self.countdownLabel.hide()

        self.shootBtn = QPushButton(self.tr("Shoot"))
        self.shootBtn.clicked.connect(self.shoot)
        self.shootBtn.setEnabled(False)
        self.shootShortcut = QShortcut(Qt.Key_Space, self, self.shoot)

        self.cancelBtn = QPushButton(self.tr("Cancel"))
        self.cancelBtn.clicked.connect(self._cancelCountdown)
        self.cancelBtn.hide()

        captureButtons = QHBoxLayout()
        captureButtons.addStretch()
        captureButtons.addWidget(self.shootBtn)
        captureButtons.addWidget(self.cancelBtn)
        captureButtons.addStretch()

        capturePage = QWidget()
        captureLayout = QVBoxLayout(capturePage)
        captureLayout.setContentsMargins(0, 0, 0, 0)
        captureLayout.addWidget(self.controls)
        captureLayout.addWidget(self.viewfinder, 1)
        captureLayout.addWidget(self.countdownLabel)
        captureLayout.addLayout(captureButtons)

        # --- preview / confirmation page ---
        self.previewLabel = QLabel()
        self.previewLabel.setAlignment(Qt.AlignCenter)
        self.previewLabel.setMinimumSize(QSize(320, 240))

        self.retakeBtn = QPushButton(self.tr("Retake"))
        self.retakeBtn.clicked.connect(self._retake)
        self.useBtn = QPushButton(self.tr("Use Photo"))
        self.useBtn.clicked.connect(self._usePhoto)
        self.useBtn.setDefault(True)

        previewButtons = QHBoxLayout()
        previewButtons.addStretch()
        previewButtons.addWidget(self.retakeBtn)
        previewButtons.addWidget(self.useBtn)
        previewButtons.addStretch()

        previewPage = QWidget()
        previewLayout = QVBoxLayout(previewPage)
        previewLayout.setContentsMargins(0, 0, 0, 0)
        previewLayout.addWidget(self.previewLabel, 1)
        previewLayout.addLayout(previewButtons)

        # --- stack ---
        self.stack = QStackedWidget()
        self._capturePageIndex = self.stack.addWidget(capturePage)
        self._previewPageIndex = self.stack.addWidget(previewPage)

        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

    def _setCountdownUi(self, active):
        self.controls.setEnabled(not active)
        self.shootBtn.setVisible(not active)
        self.cancelBtn.setVisible(active)
        self.countdownLabel.setVisible(active)

    def _showCapturePage(self):
        self._setCountdownUi(False)
        self.countdownLabel.clear()
        self.stack.setCurrentIndex(self._capturePageIndex)

    def _showPreviewPage(self, image):
        self._previewPixmap = QPixmap.fromImage(image)
        self._updatePreviewPixmap()
        self.stack.setCurrentIndex(self._previewPageIndex)

    def _updatePreviewPixmap(self):
        if self._previewPixmap is None:
            return
        scaled = self._previewPixmap.scaled(
            self.previewLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.previewLabel.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (hasattr(self, 'stack')
                and self.stack.currentIndex() == self._previewPageIndex):
            self._updatePreviewPixmap()

    # -------------------------------------------------------------- cameras

    def _populateCameras(self):
        self.cameraSelector.blockSignals(True)
        self.cameraSelector.clear()
        for cameraDevice in QMediaDevices.videoInputs():
            self.cameraSelector.addItem(cameraDevice.description(), cameraDevice.id())
        self.cameraSelector.setCurrentIndex(-1)
        self.cameraSelector.blockSignals(False)

    def _selectInitialCamera(self):
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

    def cameraChanged(self, _index):
        cameraId = self.cameraSelector.currentData()
        if cameraId is None:
            return
        for cameraDevice in QMediaDevices.videoInputs():
            if cameraDevice.id() == cameraId:
                self.setCamera(cameraDevice)

                settings = QSettings()
                settings.setValue('default_camera', cameraId)

                break

    def setCamera(self, cameraDevice):
        self.setWindowTitle(cameraDevice.description())

        # Always release any previously running camera before switching.
        self._releaseCamera()

        self._activeCameraId = cameraDevice.id()

        self.camera = QCamera(cameraDevice)
        if self.camera.isFocusModeSupported(QCamera.FocusModeAutoNear):
            self.camera.setFocusMode(QCamera.FocusModeAutoNear)
        self.camera.errorOccurred.connect(self.displayCameraError)
        self.captureSession.setCamera(self.camera)

        self.imageCapture = QImageCapture()
        self.captureSession.setImageCapture(self.imageCapture)
        self.imageCapture.readyForCaptureChanged.connect(self.readyForCapture)
        self.imageCapture.imageCaptured.connect(self.processCapturedImage)
        self.imageCapture.errorOccurred.connect(self.displayCaptureError)

        self.captureSession.setVideoOutput(self.viewfinder)

        self._populateResolutions(cameraDevice)
        self._applyCameraFormat()

        self.camera.start()

        # Opening the camera may fail immediately (in use, no permission, ...).
        if self.camera.error() != QCamera.NoError:
            self.displayCameraError()

    def _releaseCamera(self):
        """Stop timers and fully detach/destroy the camera pipeline."""
        self.countdownTimer.stop()
        self.first_capture_timer.stop()
        self._remaining = 0

        if self.imageCapture is not None:
            try:
                self.imageCapture.readyForCaptureChanged.disconnect(self.readyForCapture)
                self.imageCapture.imageCaptured.disconnect(self.processCapturedImage)
                self.imageCapture.errorOccurred.disconnect(self.displayCaptureError)
            except (RuntimeError, TypeError):
                pass

        if self.camera is not None:
            try:
                self.camera.errorOccurred.disconnect(self.displayCameraError)
            except (RuntimeError, TypeError):
                pass
            self.camera.stop()

        self.captureSession.setCamera(None)
        self.captureSession.setImageCapture(None)

        self.camera = None
        self.imageCapture = None
        self._activeCameraId = None
        self.shootBtn.setEnabled(False)

    def _onVideoInputsChanged(self):
        current_ids = [d.id() for d in QMediaDevices.videoInputs()]
        active_lost = (self._activeCameraId is not None
                       and self._activeCameraId not in current_ids)
        if active_lost:
            self._releaseCamera()
            self._showCapturePage()
            QMessageBox.warning(self, self.tr("Camera Error"),
                                self.tr("Camera disconnected"))

        # Refresh the selector to reflect the devices currently available.
        previous_id = self.cameraSelector.currentData()
        self.cameraSelector.blockSignals(True)
        self.cameraSelector.clear()
        for cameraDevice in QMediaDevices.videoInputs():
            self.cameraSelector.addItem(cameraDevice.description(), cameraDevice.id())
        index = self.cameraSelector.findData(previous_id) if previous_id else -1
        self.cameraSelector.setCurrentIndex(index)
        self.cameraSelector.blockSignals(False)

    # ----------------------------------------------------------- resolution

    def _populateResolutions(self, cameraDevice):
        self.resolutionSelector.blockSignals(True)
        self.resolutionSelector.clear()
        self.resolutionSelector.addItem(self.tr("Auto"), None)

        seen = set()
        formats = []
        for fmt in cameraDevice.videoFormats():
            res = fmt.resolution()
            key = (res.width(), res.height())
            if key in seen:
                continue
            seen.add(key)
            formats.append((res, fmt))
        formats.sort(key=lambda rf: rf[0].width() * rf[0].height(), reverse=True)
        for res, fmt in formats:
            self.resolutionSelector.addItem(f"{res.width()} x {res.height()}", fmt)

        settings = QSettings()
        saved = settings.value('camera_resolution')
        index = 0
        if saved:
            for i in range(1, self.resolutionSelector.count()):
                res = self.resolutionSelector.itemData(i).resolution()
                if f"{res.width()}x{res.height()}" == saved:
                    index = i
                    break
        self.resolutionSelector.setCurrentIndex(index)
        self.resolutionSelector.blockSignals(False)

    def _onResolutionChanged(self, _index):
        fmt = self.resolutionSelector.currentData()
        settings = QSettings()
        if fmt is None:
            settings.setValue('camera_resolution', '')
        else:
            res = fmt.resolution()
            settings.setValue('camera_resolution', f"{res.width()}x{res.height()}")
        self._applyCameraFormat()

    def _applyCameraFormat(self):
        if self.camera is None:
            return
        fmt = self.resolutionSelector.currentData()
        try:
            self.camera.setCameraFormat(fmt if fmt is not None else QCameraFormat())
        except (RuntimeError, TypeError):
            self._handleResolutionFailure()
            return
        if self.camera.error() != QCamera.NoError:
            self._handleResolutionFailure()

    def _handleResolutionFailure(self):
        errorString = self.camera.errorString() if self.camera else ""
        message = self.tr("Failed to set the selected resolution")
        if errorString:
            message = f"{message}\n{errorString}"
        QMessageBox.warning(self, self.tr("Camera Error"), message)

        # Fall back to the driver default so the live feed keeps working.
        self.resolutionSelector.blockSignals(True)
        self.resolutionSelector.setCurrentIndex(0)
        self.resolutionSelector.blockSignals(False)
        if self.camera is not None:
            self.camera.setCameraFormat(QCameraFormat())

    # ------------------------------------------------------------ countdown

    def _onCountdownChanged(self, _index):
        settings = QSettings()
        settings.setValue('camera_countdown', self.countdownSelector.currentData())

    def _countdownText(self, seconds):
        return self.tr("Capturing in {0}...").format(seconds)

    def shoot(self):
        if self.stack.currentIndex() != self._capturePageIndex:
            return
        if self.countdownTimer.isActive():
            return

        seconds = self.countdownSelector.currentData() or 0
        if seconds <= 0:
            self._beginCapture()
            return

        self._remaining = seconds
        self.countdownLabel.setText(self._countdownText(seconds))
        self._setCountdownUi(True)
        self.countdownTimer.start()

    def _onCountdownTick(self):
        self._remaining -= 1
        if self._remaining > 0:
            self.countdownLabel.setText(self._countdownText(self._remaining))
        else:
            self.countdownTimer.stop()
            self._beginCapture()

    def _cancelCountdown(self):
        self.countdownTimer.stop()
        self._remaining = 0
        self.countdownLabel.clear()
        self._setCountdownUi(False)

    def _beginCapture(self):
        self._setCountdownUi(False)
        self.countdownLabel.clear()

        if self.imageCapture is None or not self.imageCapture.isReadyForCapture():
            QMessageBox.warning(self, self.tr("Camera Error"),
                                self.tr("Camera not available"))
            return

        self.first_capture_timer.start(2500)
        self.imageCapture.capture()

    # ------------------------------------------------------------- capture

    def readyForCapture(self, ready):
        self.shootBtn.setEnabled(ready)

    def processCapturedImage(self, _requestId, img):
        self.first_capture_timer.stop()

        self._pendingImage = img
        self._showPreviewPage(img)

    def _usePhoto(self):
        if self._pendingImage is None:
            return
        self.image = self._pendingImage
        self.accept()

    def _retake(self):
        self._pendingImage = None
        self._previewPixmap = None
        self.previewLabel.clear()
        self._showCapturePage()

    # -------------------------------------------------------------- errors

    def firstCaptureTimeout(self):
        self._setCountdownUi(False)
        QMessageBox.warning(self, self.tr("Camera Error"),
            self.tr("Camera not available or disabled by antivirus"))

    def displayCameraError(self, *_args):
        if self.camera is None or self.camera.error() == QCamera.NoError:
            return

        errorString = self.camera.errorString()
        self._releaseCamera()
        self._showCapturePage()
        QMessageBox.warning(self, self.tr("Camera Error"),
                            errorString or self.tr("Camera not available"))

    def displayCaptureError(self, _id, _error, errorString):
        self.first_capture_timer.stop()
        self._setCountdownUi(False)
        QMessageBox.warning(self, self.tr("Image Capture Error"), errorString)

    # ---------------------------------------------------------------- close

    def done(self, r):
        self._releaseCamera()
        super().done(r)
