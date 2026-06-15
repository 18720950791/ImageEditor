import os

from PySide6.QtCore import QSettings, QFileInfo, Qt, QStandardPaths, QDir, QCoreApplication
from PySide6.QtGui import QIcon, QAction, QBrush, QColor, QImage
from PySide6.QtWidgets import (QApplication, QStyle, QMessageBox, QDialog, QFileDialog,
                                QHBoxLayout, QGroupBox, QRadioButton, QVBoxLayout,
                                QFormLayout, QComboBox, QSpinBox, QLineEdit, QPushButton,
                                QDialogButtonBox, QProgressDialog)

from ImageEditor import ImageEditorDialog, SettingsDialog
from ImageProxy import ImageProxy
from ImageScrollLabel import ImageScrollLabel
from Tools.Gui import getSaveFileName, ColorButton
from Tools.misc import saveImageFilters, batchExportFormats

IMAGE_PATH = QStandardPaths.standardLocations(QStandardPaths.PicturesLocation)[0]


class WindowSettingsDialog(SettingsDialog):

    def __init__(self, parent=None):
        super().__init__(parent)

        settings = QSettings()

        color = settings.value('mainwindow/transparent_color', QColor(Qt.white), type=QColor)
        self.transparentColorButton = ColorButton(color, self)

        self.transparentRadio = QRadioButton(self.tr("Transparent"))
        self.transparentRadio.toggled.connect(self.transparentRadioToggled)
        self.colorRadio = QRadioButton(self.tr("Color"))
        if settings.value('mainwindow/transparent_store', True, type=bool):
            self.transparentRadio.setChecked(True)
        else:
            self.colorRadio.setChecked(True)

        colorBtnLayout = QHBoxLayout()
        colorBtnLayout.addWidget(self.colorRadio)
        colorBtnLayout.addWidget(self.transparentColorButton)
        colorBtnLayout.setAlignment(Qt.AlignLeft)

        colorLayout = QHBoxLayout()
        colorLayout.addWidget(self.transparentRadio)
        colorLayout.addLayout(colorBtnLayout)

        colorGroup = QGroupBox(self.tr("Image background color"), self)
        colorGroup.setLayout(colorLayout)

        self.main_layout.addRow(colorGroup)

    def transparentRadioToggled(self, checked):
        self.transparentColorButton.setDisabled(checked)

    def save(self):
        settings = QSettings()

        settings.setValue('mainwindow/transparent_store', self.transparentRadio.isChecked())
        settings.setValue('mainwindow/transparent_color', self.transparentColorButton.color())

        super().save()


class BatchExportDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(self.tr("Batch Export Folder"))
        self.setMinimumWidth(420)

        settings = QSettings()
        last_dir = settings.value('images/batch_export_dir',
                                  QStandardPaths.standardLocations(QStandardPaths.PicturesLocation)[0])

        # Output directory
        self.dirEdit = QLineEdit(last_dir)
        browseBtn = QPushButton(self.tr("Browse..."))
        browseBtn.clicked.connect(self._browseDir)
        dirLayout = QHBoxLayout()
        dirLayout.addWidget(self.dirEdit)
        dirLayout.addWidget(browseBtn)

        # Target format
        self.formatCombo = QComboBox()
        self._formats = batchExportFormats()
        for name, ext in self._formats:
            self.formatCombo.addItem(f"{name} (*{ext})", ext)

        # Max dimensions
        self.maxWidthSpin = QSpinBox()
        self.maxWidthSpin.setRange(0, 99999)
        self.maxWidthSpin.setSpecialValueText(self.tr("No limit"))
        self.maxWidthSpin.setValue(0)

        self.maxHeightSpin = QSpinBox()
        self.maxHeightSpin.setRange(0, 99999)
        self.maxHeightSpin.setSpecialValueText(self.tr("No limit"))
        self.maxHeightSpin.setValue(0)

        # Quality
        self.qualitySpin = QSpinBox()
        self.qualitySpin.setRange(1, 100)
        self.qualitySpin.setValue(90)

        self.formatCombo.currentIndexChanged.connect(self._formatChanged)
        self._formatChanged(0)

        # Layout
        formLayout = QFormLayout()
        formLayout.addRow(self.tr("Output directory:"), dirLayout)
        formLayout.addRow(self.tr("Target format:"), self.formatCombo)
        formLayout.addRow(self.tr("Max width (px):"), self.maxWidthSpin)
        formLayout.addRow(self.tr("Max height (px):"), self.maxHeightSpin)
        formLayout.addRow(self.tr("Quality (JPEG/WebP):"), self.qualitySpin)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self._validate)
        buttonBox.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(formLayout)
        layout.addWidget(buttonBox)

    def _browseDir(self):
        dir_ = QFileDialog.getExistingDirectory(
            self, self.tr("Select output directory"), self.dirEdit.text())
        if dir_:
            self.dirEdit.setText(dir_)

    def _formatChanged(self, index):
        ext = self._formats[index][1]
        quality_enabled = ext in ('.jpg', '.webp')
        self.qualitySpin.setEnabled(quality_enabled)

    def _validate(self):
        dir_ = self.dirEdit.text().strip()
        if not dir_ or not QDir(dir_).exists():
            QMessageBox.warning(self, self.tr("Invalid Directory"),
                                self.tr("Please select a valid output directory."))
            return
        self.accept()

    def outputDir(self):
        return self.dirEdit.text().strip()

    def formatExtension(self):
        return self._formats[self.formatCombo.currentIndex()][1]

    def formatName(self):
        return self._formats[self.formatCombo.currentIndex()][0]

    def maxWidth(self):
        return self.maxWidthSpin.value()

    def maxHeight(self):
        return self.maxHeightSpin.value()

    def quality(self):
        return self.qualitySpin.value()


class ImageEditorWindow(ImageEditorDialog):

    def __init__(self, parent=None):
        super().__init__(parent, scrollpanel=True)

        self.navigationMenu.removeAction(self.prevRecordAct)
        self.navigationMenu.removeAction(self.nextRecordAct)

        self.setWindowIcon(QIcon(':/slide.png'))
        
        self.viewer.doubleClicked.connect(self.viewerDoubleClicked)

        self.scrollPanel.hide()

        self.saveImageConnected = False

    def createActions(self):
        super().createActions()

        style = QApplication.style()
        icon = style.standardIcon(QStyle.SP_DirOpenIcon)
        self.openFolderAct = QAction(icon, self.tr("Open folder..."), self, triggered=self.openFolder)
        self.batchExportAct = QAction(self.tr("Batch export folder..."), self, triggered=self.batchExport)

    def createMenus(self):
        super().createMenus()

        self.fileMenu.insertAction(self.saveAct, self.openFolderAct)
        self.fileMenu.insertAction(self.saveAct, self.batchExportAct)

    def createToolBar(self):
        super().createToolBar()

        self.toolBar.insertAction(self.saveAct, self.openFileAct)

    def viewerDoubleClicked(self):
        if not self.hasImage():
            self.openFile()

    def openFile(self):
        if super().openFile():
            self.scrollPanel.clear()
            self.scrollPanel.hide()
            self.navigationMenu.setEnabled(False)
            self.batchExportAct.setDisabled(True)

            self.imageSaved.connect(self.saveImage)
            self.saveImageConnected = True

    def openFolder(self):
        settings = QSettings()
        last_dir = settings.value('images/last_dir', IMAGE_PATH)

        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Open image folder"), last_dir)
        if folder:
            dir_ = QDir(folder)
            filter_ = ('*.png', '*.jpg', '*.jpeg', '*.bmp',
                       '*.tif', '*.tiff', '*.gif', '*.webp')
            files = dir_.entryInfoList(filter_, QDir.Files, QDir.Name)
            if len(files) > 0:
                proxy = ImageProxy()
                for file in files:
                    image = ImageScrollLabel(field=file.filePath(), title=file.fileName())
                    image.loadFromFile(file.filePath())
                    image.imageEdited.connect(self.imageEdited)
                    proxy.append(image)
    
                proxy.setCurrent(files[0].filePath())
                self.setImageProxy(proxy)
                self.scrollPanel.show()
                self.navigationMenu.setEnabled(True)

                if self.saveImageConnected:
                    self.imageSaved.disconnect(self.saveImage)
                    self.saveImageConnected = False

    def loadFromFile(self, fileName):
        if self.isChanged:
            result = QMessageBox.warning(
                self, self.tr("Save"),
                self.tr("Image was changed. Save changes?"),
                QMessageBox.Save | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel)
            if result == QMessageBox.Save:
                self.save(confirm_save=False)
            elif result == QMessageBox.Cancel:
                return

        image = QImage(fileName)
        self.setImage(image)

        file_info = QFileInfo(fileName)
        settings = QSettings()
        settings.setValue('images/last_dir', file_info.absolutePath())

        file_title = file_info.fileName()
        self.setTitle(file_title)

        self.origFileName = fileName

        self.undo_stack.clear()
        self.undoAct.setDisabled(True)
        self.redoAct.setDisabled(True)
        self.isChanged = False
        # self.markWindowTitle(self.isChanged)
        self._updateEditActions()

    def imageEdited(self, image):
        image.image.save(image.field)

    def saveImage(self, image):
        image.save(self.origFileName)

    def saveAs(self):
        fileName, _selectedFilter = getSaveFileName(
            self, 'images', self.name, IMAGE_PATH, saveImageFilters())
        if fileName:
            self._pixmapHandle.pixmap().save(fileName)

            file_info = QFileInfo(fileName)
            settings = QSettings()
            settings.setValue('images/last_dir', file_info.absolutePath())

            file_title = file_info.fileName()
            self.setTitle(file_title)

            self.origFileName = fileName

            self.isChanged = False

    def settings(self):
        dlg = WindowSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            color = dlg.windowColorButton.color()
            self.viewer.setBackgroundBrush(QBrush(color))

    def _updateEditActions(self):
        super()._updateEditActions()

        inCrop = self.cropAct.isChecked()
        inRotate = self.rotateAct.isChecked()
        hasFolder = self.proxy is not None and len(self.proxy.images()) > 0
        self.openFolderAct.setDisabled(inCrop or inRotate)
        self.batchExportAct.setEnabled(hasFolder and not inCrop and not inRotate)

    def batchExport(self):
        if not self.proxy or not self.proxy.images():
            QMessageBox.warning(self, self.tr("No Folder"),
                                self.tr("Please open a folder first."))
            return

        dlg = BatchExportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        output_dir = dlg.outputDir()
        ext = dlg.formatExtension()
        fmt = dlg.formatName()
        max_w = dlg.maxWidth()
        max_h = dlg.maxHeight()
        quality = dlg.quality()

        settings = QSettings()
        settings.setValue('images/batch_export_dir', output_dir)

        images = self.proxy.images()
        total = len(images)
        failed = []
        exported = 0
        cancelled = False

        progress = QProgressDialog(
            self.tr("Exporting images..."), self.tr("Cancel"), 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        for i, label in enumerate(images):
            if progress.wasCanceled():
                cancelled = True
                break

            progress.setLabelText(
                self.tr("Exporting image %1 of %2: %3").arg(
                    str(i + 1), str(total), QFileInfo(label.field).fileName()))
            progress.setValue(i)
            QCoreApplication.processEvents()

            try:
                image = QImage(label.field)
                if image.isNull():
                    raise RuntimeError(self.tr("Could not read image"))

                if max_w > 0 or max_h > 0:
                    target_w = max_w if max_w > 0 else image.width()
                    target_h = max_h if max_h > 0 else image.height()
                    if image.width() > target_w or image.height() > target_h:
                        image = image.scaled(target_w, target_h,
                                             Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation)

                base_name = QFileInfo(label.field).completeBaseName()
                out_path = os.path.join(output_dir, base_name + ext)

                q = quality if ext in ('.jpg', '.webp') else -1
                if not image.save(out_path, fmt, q):
                    raise RuntimeError(self.tr("Failed to save image"))

                exported += 1

            except Exception as e:
                failed.append((QFileInfo(label.field).fileName(), str(e)))

        progress.setValue(total)
        progress.close()

        # Build summary message
        parts = []
        parts.append(self.tr("Exported: %1 of %2").arg(str(exported), str(total)))
        if cancelled:
            parts.append(self.tr("Cancelled: %1 remaining").arg(str(total - exported - len(failed))))
        if failed:
            parts.append(self.tr("Failed: %1").arg(str(len(failed))))
            for name, err in failed:
                parts.append(f"  \u2022 {name}: {err}")

        msg = "\n".join(parts)
        if failed or cancelled:
            QMessageBox.warning(self, self.tr("Batch Export"), msg)
        else:
            QMessageBox.information(self, self.tr("Batch Export"), msg)
