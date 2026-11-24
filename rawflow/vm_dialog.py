"""Diálogo «Crear máquina virtual» (UTM, Mac)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from . import theme as T
from . import vm
from .widgets import font

DIALOG_QSS = f"""
QDialog {{ background: {T.BG}; }}
QComboBox, QSpinBox {{
    background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: 10px;
    padding: 8px 12px; font-size: 14px;
}}
QComboBox:focus, QSpinBox:focus {{ border: 1px solid {T.ACCENT}; }}
QComboBox QAbstractItemView {{ background: {T.SURFACE_2}; selection-background-color: {T.ACCENT};
    selection-color: {T.ACCENT_INK}; }}
QPlainTextEdit {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 10px;
    padding: 8px; color: {T.MUTED}; }}
"""


class CreateWorker(QThread):
    log = Signal(str)
    done = Signal(str, str)     # uuid, error

    def __init__(self, spec: vm.VMSpec, parent=None):
        super().__init__(parent)
        self.spec = spec

    def run(self):
        try:
            uuid = vm.create_vm(self.spec, self.log.emit)
            self.done.emit(uuid, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit("", str(e))


class CreateVMDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crear máquina virtual")
        self.setMinimumWidth(620)
        self.setStyleSheet(DIALOG_QSS)
        self.worker: CreateWorker | None = None
        rec = vm.recommended()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        title = QLabel("CREAR MÁQUINA VIRTUAL", objectName="sectionTitle")
        lay.addWidget(title)
        ver = vm.utm_version()
        self.utm_line = QLabel(f"UTM {ver} instalado" if ver else
                               "UTM no está instalado (descárgalo de mac.getutm.app)")
        self.utm_line.setObjectName("chipOk" if ver else "chipWarn")
        lay.addWidget(self.utm_line, 0, Qt.AlignmentFlag.AlignLeft)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        iso_row = QHBoxLayout()
        self.iso = QComboBox()
        self.iso.setMinimumWidth(380)
        for p in vm.find_isos():
            self.iso.addItem(p.name, str(p))
            self.iso.setItemData(self.iso.count() - 1, str(p), Qt.ItemDataRole.ToolTipRole)
        self.iso.currentIndexChanged.connect(self._iso_changed)
        iso_row.addWidget(self.iso, 1)
        pick = QPushButton("Elegir…")
        pick.clicked.connect(self._pick_iso)
        iso_row.addWidget(pick)
        form.addRow("ISO", iso_row)

        self.kind = QLabel("", objectName="muted")
        form.addRow("Sistema", self.kind)

        self.name = QLineEdit()
        form.addRow("Nombre", self.name)

        self.mem = QSpinBox(suffix=" GB", minimum=2, maximum=rec["max_memory_mb"] // 1024)
        self.mem.setValue(rec["memory_mb"] // 1024)
        self.cpu = QSpinBox(suffix=" núcleos", minimum=1, maximum=rec["max_cores"])
        self.cpu.setValue(rec["cores"])
        self.disk = QSpinBox(suffix=" GB", minimum=16, maximum=1024, singleStep=16)
        self.disk.setValue(rec["disk_gb"])
        hw = QHBoxLayout()
        hw.setSpacing(10)
        for w in (self.mem, self.cpu, self.disk):
            hw.addWidget(w)
        form.addRow("Memoria · CPU · Disco", hw)
        form.addRow("", QLabel("Red compartida con el Mac. El disco solo ocupa lo que se use.",
                               objectName="faint"))
        lay.addLayout(form)

        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setFont(font(12, mono=True))
        self.logbox.setFixedHeight(130)
        self.logbox.hide()
        lay.addWidget(self.logbox)

        brow = QHBoxLayout()
        brow.addStretch(1)
        self.close_btn = QPushButton("Cerrar")
        self.close_btn.clicked.connect(self.reject)
        brow.addWidget(self.close_btn)
        self.go = QPushButton("Crear y arrancar", objectName="primary")
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.clicked.connect(self._create)
        brow.addWidget(self.go)
        lay.addLayout(brow)

        if self.iso.count() == 0:
            self.kind.setText("No encontré ninguna ISO en Documentos, Descargas o Escritorio.")
        self._iso_changed()
        self.go.setEnabled(bool(ver) and self.iso.count() > 0)

    # ------------------------------------------------------------------
    def _pick_iso(self):
        path, _ = QFileDialog.getOpenFileName(self, "Elige la ISO", str(Path.home() / "Documents"),
                                              "Imágenes de disco (*.iso)")
        if path:
            self.iso.insertItem(0, Path(path).name, path)
            self.iso.setCurrentIndex(0)
            self.go.setEnabled(bool(vm.utm_version()))

    def _iso_changed(self, *_):
        p = self.iso.currentData()
        if not p:
            return
        info = vm.inspect_iso(Path(p))
        self.info = info
        self.kind.setText(info.label)
        self.name.setText(vm.unique_name(vm.default_name(info)))

    def _log(self, s: str):
        self.logbox.show()
        self.logbox.appendPlainText(s)

    def _create(self):
        p = self.iso.currentData()
        if not p:
            return
        name = self.name.text().strip() or vm.default_name(self.info)
        spec = vm.VMSpec(Path(p), vm.unique_name(name), self.mem.value() * 1024,
                         self.cpu.value(), self.disk.value(), self.info)
        if self.info.emulated:
            r = QMessageBox.question(
                self, "Crear máquina virtual",
                "Esta ISO es para PC (x64). En este Mac funcionará emulada: la instalación y el uso "
                "serán bastante más lentos que en un PC.\n\n"
                + ("Es una versión modificada no oficial de Windows.\n\n" if "tomex" in p.lower() else "")
                + "¿Crear la máquina virtual igualmente?")
            if r != QMessageBox.StandardButton.Yes:
                return
        for w in (self.iso, self.name, self.mem, self.cpu, self.disk, self.go):
            w.setEnabled(False)
        self.go.setText("Creando…")
        self.worker = CreateWorker(spec, self)
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, uuid: str, err: str):
        self.worker = None
        if err:
            self._log(f"✗  {err}")
            QMessageBox.warning(self, "Crear máquina virtual", err)
            for w in (self.iso, self.name, self.mem, self.cpu, self.disk, self.go):
                w.setEnabled(True)
            self.go.setText("Reintentar")
            return
        self.go.setText("Lista ✓")
        self.close_btn.setText("Listo")
        QMessageBox.information(self, "Máquina virtual encendida",
                                "La máquina virtual está arrancando en UTM.\n\n" + vm.BOOT_TIPS)

    def reject(self):
        if self.worker:
            QMessageBox.information(self, "Crear máquina virtual",
                                    "Espera a que termine de crearse la máquina virtual.")
            return
        super().reject()


# ==========================================================================
# «Instalar máquina virtual»: todo automático
# ==========================================================================
class InstallWorker(QThread):
    log = Signal(str)
    done = Signal(str, str)     # uuid, error

    def __init__(self, iso: Path, user: str, parent=None):
        super().__init__(parent)
        self.iso, self.user = iso, user
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        """Todo en este Mac y con los instaladores de la carpeta «Instaladores»; cada máquina sale igual:
        Windows limpio (sin apps de reclamo), los programas y RawFlow instalados, escritorio y barra con solo los
        programas de RawFlow, inicio de sesión guiado (una cosa cada vez), reinicio y «base» guardada.
        Con base: copia en segundos, puesta al día, y solo se pide lo nuevo."""
        import json

        from . import vm_auto
        from . import vm_programas as vp
        vp.BUSY = True                             # si empieza un evento mientras tanto, no apaga esta máquina
        try:
            self._run(json, vm_auto, vp)
        finally:
            vp.BUSY = False

    def _run(self, json, vm_auto, vp):
        try:
            clon = vp.from_base("RawFlow Windows", self.log.emit)   # base con programas y sesiones: segundos
            if clon:
                self.log.emit("Poniendo al día la copia: RawFlow, Windows limpio, escritorio y barra…")
                self.log.emit("   RawFlow: " + vp.update_exe(clon, self.log.emit))
                nuevos = vp.install_missing(clon, self.log.emit)   # instaladores añadidos después de la base
                try:
                    falta_dxo = not json.loads(vp.detect(clon)).get("DxO PureRAW")
                except Exception:  # noqa: BLE001
                    falta_dxo = False
                pide = tuple(n for n in nuevos if n in vp.SIGN_IN)
                if (pide or falta_dxo) and not self._stop:
                    self._guide(vp, clon, pide)
                if (nuevos or falta_dxo) and not self._stop:
                    self._finish(vp, clon)                 # la base se renueva con lo nuevo
                self.done.emit(clon, "")
                return
            uuid = vm_auto.install_windows(self.iso, self.user, self.log.emit,
                                           should_stop=lambda: self._stop, name="RawFlow Windows")
            # 2.ª parte: Windows limpio, los programas de «Instaladores» y RawFlow, sin clics
            if uuid and not self._stop and vp.installers_root():
                self.log.emit("Dentro de Windows: quito lo que no sirve e instalo los programas de «Instaladores» y "
                              "RawFlow (sin clics)…")
                arch = "arm64" if vm.inspect_iso(self.iso).arch == "aarch64" else "amd64"
                res = vp.provision(uuid, self.log.emit, arch=arch)
                for k, v in res.items():
                    self.log.emit(f"   {k}: {v}")
                if not self._stop:
                    self._guide(vp, uuid, None)            # 3.ª: iniciar sesión, una cosa cada vez
                if not self._stop:
                    self._finish(vp, uuid)                 # 4.ª: barra fija (reinicio) y base guardada
            self.done.emit(uuid, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit("", str(e))

    def _guide(self, vp, vm_id: str, only):
        import subprocess
        self.log.emit("Ahora te toca iniciar sesión, UNA cosa cada vez: mira la ventana de Windows; el aviso de abajo "
                      "dice qué hacer y, al cerrar cada programa, se abre el siguiente.")
        subprocess.run(["open", "-a", "UTM"], capture_output=True)   # que la ventana de Windows quede a la vista
        res = vp.guided_setup(vm_id, self.log.emit, only=only)
        for k, v in res.items():
            self.log.emit(f"   {k}: {v}")

    def _finish(self, vp, vm_id: str):
        res = vp.finish_setup(vm_id, self.log.emit)
        for k, v in res.items():
            if k != "RawFlow ve":
                self.log.emit(f"   {k}: {v}")
        try:
            ve = __import__("json").loads(res.get("RawFlow ve", "{}"))
            self.log.emit("RawFlow ve en Windows: " + ", ".join(k for k, v in ve.items() if v) + ".")
        except Exception:  # noqa: BLE001
            pass


class InstallVMDialog(QDialog):
    """Un clic: crea la VM de Windows, la instala sola con cuenta local y drivers."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        from . import vm_auto
        self.settings = settings
        self.setWindowTitle("Instalar máquina virtual")
        self.setMinimumWidth(640)
        self.setStyleSheet(DIALOG_QSS)
        self.worker: InstallWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        lay.addWidget(QLabel("INSTALAR MÁQUINA VIRTUAL", objectName="sectionTitle"))
        sub = QLabel("Todo en este Mac, con los instaladores de la carpeta «Instaladores»: crea la máquina, instala "
                     "Windows, quita lo que no sirve, instala los programas y RawFlow, y deja en el escritorio y la "
                     "barra solo esos programas. Después te guía para iniciar sesión, una cosa cada vez. "
                     "La próxima vez copia esa máquina en segundos, con las sesiones ya iniciadas.", objectName="muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.iso = vm_auto.find_windows_iso()
        ver = vm.utm_version()
        rec = vm.recommended()
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        if self.iso:
            info = vm.inspect_iso(self.iso)
            form.addRow("Windows", QLabel(f"{self.iso.name}\n{info.label}"))
        else:
            form.addRow("Windows", QLabel("No encontré una ISO de Windows en Documentos, Descargas o Escritorio.",
                                          objectName="chipWarn"))
        self.user = QLineEdit(self.settings.value("vm_user", "Alejandro"))
        form.addRow("Cuenta local", self.user)
        form.addRow("Equipo", QLabel(f"{rec['memory_mb'] // 1024} GB · {rec['cores']} núcleos · disco 64 GB · "
                                     f"red compartida"))
        form.addRow("UTM", QLabel(f"{ver} instalado" if ver else "No está instalado (mac.getutm.app)"))
        lay.addLayout(form)
        note = QLabel("Al instalar aceptas los términos de licencia de Windows y de los drivers de UTM. "
                      "Windows queda sin activar (actívalo luego con tu clave).", objectName="faint")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setFont(font(12, mono=True))
        self.logbox.setFixedHeight(170)
        self.logbox.hide()
        lay.addWidget(self.logbox)

        brow = QHBoxLayout()
        if not self.iso:
            get = QPushButton("Descargar Windows 11 ARM")
            get.clicked.connect(lambda: __import__("webbrowser").open(vm_auto.WIN_PAGE))
            brow.addWidget(get)
        brow.addStretch(1)
        self.close_btn = QPushButton("Cerrar")
        self.close_btn.clicked.connect(self.reject)
        brow.addWidget(self.close_btn)
        self.go = QPushButton("Instalar", objectName="primary")
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.clicked.connect(self._start)
        self.go.setEnabled(bool(self.iso and ver))
        brow.addWidget(self.go)
        lay.addLayout(brow)

    def _log(self, s: str):
        self.logbox.show()
        self.logbox.appendPlainText(s)

    def _start(self):
        user = self.user.text().strip() or "Usuario"
        if not __import__("re").fullmatch(r"[A-Za-z0-9 ._\-ÁÉÍÓÚáéíóúÑñ]{1,20}", user):
            QMessageBox.warning(self, "Instalar máquina virtual",
                                "El nombre de la cuenta solo puede tener letras, números y espacios (máx. 20).")
            return
        self.settings.setValue("vm_user", user)
        self.user.setEnabled(False)
        self.go.setEnabled(False)
        self.go.setText("Instalando…")
        self.close_btn.setText("Ocultar")
        self.worker = InstallWorker(self.iso, user, self)
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, uuid: str, err: str):
        self.worker = None
        if err:
            self._log(f"✗  {err}")
            QMessageBox.warning(self, "Instalar máquina virtual", err)
            self.go.setEnabled(True)
            self.user.setEnabled(True)
            self.go.setText("Reintentar")
            self.close_btn.setText("Cerrar")
            return
        self.go.setText("Lista ✓")
        self.close_btn.setText("Listo")

    def reject(self):
        if self.worker:
            # se puede ocultar: el hilo sigue y la VM se instala sola
            self.hide()
            return
        super().reject()
