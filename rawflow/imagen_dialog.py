"""Diálogo «Configurar Imagen»: clave API (en el Llavero del Mac; cifrada en Windows), perfil y tipo de fotografía."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from . import imagen
from . import theme as T

QSS = f"""
QDialog {{ background: {T.BG}; }}
QComboBox {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: 10px;
    padding: 8px 12px; font-size: 14px; }}
QComboBox QAbstractItemView {{ background: {T.SURFACE_2}; selection-background-color: {T.ACCENT};
    selection-color: {T.ACCENT_INK}; }}
#primary {{ padding: 9px 22px; font-size: 14px; border-radius: 10px; }}
"""


class ProfilesWorker(QThread):
    done = Signal(list, str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key

    def run(self):
        try:
            self.done.emit(imagen.Client(self.key).profiles(), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit([], str(e))


class ImagenDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.worker = None
        self.setWindowTitle("Configurar Imagen")
        self.setMinimumWidth(600)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        lay.addWidget(QLabel("IMAGEN AI  ·  PASO 4", objectName="sectionTitle"))
        intro = QLabel("Con tu clave API, RawFlow sube los DNG de «2 DNGs» a Imagen, los edita con tu perfil y guarda "
                       "los JPEG en «1 Original/Imagen AI» (de ahí siguen a Retouch4me). También deja los .xmp junto a los DNG. "
                       "Imagen trabaja en la nube: necesita internet y cobra por foto, como en su app. "
                       "La clave se guarda cifrada en este equipo; para quitarla, borra el campo y pulsa Guardar.",
                       objectName="muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        krow = QHBoxLayout()
        self.key = QLineEdit(imagen.get_api_key())
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Pega aquí tu clave API de Imagen")
        krow.addWidget(self.key, 1)
        self.test = QPushButton("Comprobar")
        self.test.clicked.connect(self._load_profiles)
        krow.addWidget(self.test)
        form.addRow("Clave API", krow)
        self.profile = QComboBox()
        self.profile.setEditable(True)
        self.profile.addItem(settings.value("imagen_profile", imagen.DEFAULT_PROFILE))
        form.addRow("Perfil", self.profile)
        self.ptype = QComboBox()
        self.ptype.addItems(list(imagen.PHOTO_TYPES))
        self.ptype.setCurrentText(settings.value("imagen_type", imagen.DEFAULT_TYPE))
        form.addRow("Tipo de fotografía", self.ptype)
        form.addRow("Herramientas", QLabel("Recortar · Enderezar · Máscara de sujeto", objectName="muted"))
        lay.addLayout(form)

        self.msg = QLabel("¿No tienes clave? Pídela al soporte de Imagen (support@imagen-ai.com) "
                          "indicando que quieres usar la API.", objectName="faint")
        self.msg.setWordWrap(True)
        self.msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.msg)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("Guardar", objectName="primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        lay.addLayout(row)

    def _load_profiles(self):
        k = self.key.text().strip()
        if not k or self.worker:
            return
        self.test.setEnabled(False)
        self.msg.setText("Comprobando la clave con Imagen…")
        self.worker = ProfilesWorker(k, self)
        self.worker.done.connect(self._profiles_done)
        self.worker.start()

    def _profiles_done(self, profiles: list, err: str):
        self.worker = None
        self.test.setEnabled(True)
        if err:
            self.msg.setText(f"✗  {err}")
            return
        cur = self.profile.currentText()
        self.profile.clear()
        names = [p.get("profile_name", "") for p in profiles if p.get("profile_name")]
        self.profile.addItems(names)
        if cur in names:
            self.profile.setCurrentText(cur)
        self.msg.setText(f"✓  Clave correcta · {len(names)} perfiles en tu cuenta.")

    def _save(self):
        try:
            imagen.set_api_key(self.key.text())
        except imagen.ImagenError as e:
            self.msg.setText(f"✗  {e}")
            return
        self.settings.setValue("imagen_profile", self.profile.currentText().strip() or imagen.DEFAULT_PROFILE)
        self.settings.setValue("imagen_type", self.ptype.currentText())
        self.accept()

    def reject(self):
        if self.worker:
            self.worker.wait(3000)
        super().reject()
