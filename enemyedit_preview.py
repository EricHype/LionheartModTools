"""The creature preview: export a model once, then show and animate it in the window.

* **ModelCache** turns a `Model=` reference into a glTF the viewer can load. It reads the
  model's `.mdl16` for the mesh and its clips (the game's own list, not a folder search),
  copies those `.gr2` files out of the archive or the mod, and runs
  `gr2_to_gltf.export_model`. An export takes about six seconds, so results are kept in
  `%LOCALAPPDATA%\\LionheartModTools\\model-cache`, keyed by a fingerprint of the source
  bytes: a mod that changes a model gets a fresh export, and nothing else is redone.
* **ViewerServer** serves `viewer/` and the cache on 127.0.0.1. Chromium will not load ES
  modules from `file://`, which three.js needs.
* **PreviewWidget** is the `QWebEngineView` with the clip picker, play and speed.

WebGL needs the real display: under `QT_QPA_PLATFORM=offscreen` Chromium loses its context
(E0), so automated checks run the window off-screen instead.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

import gr2_to_gltf
from enemyedit_core import model_info
from icontool import Library

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "viewer"


def cache_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LionheartModTools" / "model-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def safe(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", ref).strip("_")


class ModelCache:
    def __init__(self, lib: Library, root: Path | None = None):
        self.lib = lib
        self.root = Path(root) if root else cache_dir()

    def sources(self, ref: str) -> tuple[str, list[str]]:
        info = model_info(self.lib, ref)
        if not info.exists or not info.mesh:
            raise FileNotFoundError(f"no model {ref}")
        mesh = "Resources/" + info.mesh + ".MODEL.GR2"
        clips = ["Resources/" + c + ".ANIMATION.GR2" for c in info.clips]
        return mesh, clips

    def fingerprint(self, ref: str) -> str:
        mesh, clips = self.sources(ref)
        h = hashlib.sha1(self.lib.read("Cache/Models/" + ref + ".mdl16"))
        for rel in [mesh] + clips:
            h.update(rel.lower().encode())
            h.update(self.lib.read(rel))
        return h.hexdigest()[:12]

    def path_for(self, ref: str) -> Path:
        return self.root / f"{safe(ref)}-{self.fingerprint(ref)}.gltf"

    def cached(self, ref: str) -> Path | None:
        p = self.path_for(ref)
        return p if p.is_file() and p.with_suffix(".bin").is_file() else None

    def build(self, ref: str) -> Path:
        """Export `ref` if it is not cached; returns the .gltf path."""
        out = self.path_for(ref)
        if out.is_file() and out.with_suffix(".bin").is_file():
            return out
        mesh, clips = self.sources(ref)
        src = self.root / "src" / out.stem
        paths = []
        for rel in [mesh] + clips:
            p = src / Path(self.lib.canonical(rel))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.lib.read(rel))
            paths.append(p)
        tmp = out.with_name(out.stem + ".partial.gltf")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            gr2_to_gltf.export_model(str(paths[0]), str(tmp), [str(p) for p in paths[1:]])
        # the .gltf names its .bin; rename both, keeping the reference right
        data = json.loads(tmp.read_text())
        data["buffers"][0]["uri"] = out.with_suffix(".bin").name
        tmp.with_suffix(".bin").replace(out.with_suffix(".bin"))
        out.write_text(json.dumps(data))
        tmp.unlink()
        shutil.rmtree(src, ignore_errors=True)   # the copies were only for the exporter
        return out


class ExportThread(QThread):
    done = Signal(str, str, str)          # ref, gltf path or "", error or ""

    def __init__(self, cache: ModelCache, ref: str):
        super().__init__()
        self.cache, self.ref = cache, ref

    def run(self):
        try:
            self.done.emit(self.ref, str(self.cache.build(self.ref)), "")
        except Exception as e:            # shown in the preview, never fatal
            self.done.emit(self.ref, "", f"{type(e).__name__}: {e}")


class ViewerServer:
    """Serves /viewer/... and /cache/... on a free localhost port, for the app's lifetime."""

    def __init__(self, cache_root: Path):
        root = Path(cache_root)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def translate_path(self, path):
                path = path.split("?", 1)[0].split("#", 1)[0]
                from urllib.parse import unquote
                path = unquote(path)
                if path.startswith("/cache/"):
                    return str(root / path[len("/cache/"):])
                return str(VIEWER / path.lstrip("/"))

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}/{path.lstrip('/')}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class PreviewWidget(QWidget):
    """The 3D view plus its controls. `show_model(ref)` exports if needed, then loads."""
    clips_changed = Signal(list)

    def __init__(self, cache: ModelCache, server: ViewerServer, parent=None):
        super().__init__(parent)
        self.cache, self.server = cache, server
        self.ref = ""
        self.clips: list[str] = []
        self._threads: set = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.view.setPage(QWebEnginePage(self.view))
        self.view.setMinimumSize(320, 280)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.clip_box = QComboBox()
        self.clip_box.currentTextChanged.connect(self._play_current)
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.loop.toggled.connect(lambda _on: self._play_current(self.clip_box.currentText()))
        self.speed = QComboBox()
        self.speed.addItems(["0.25x", "0.5x", "1x", "2x"])
        self.speed.setCurrentText("1x")
        self.speed.currentTextChanged.connect(lambda _t: self._play_current(self.clip_box.currentText()))
        self.replay = QPushButton("Play again")
        self.replay.clicked.connect(lambda: self._play_current(self.clip_box.currentText()))
        row.addWidget(QLabel("Animation"))
        row.addWidget(self.clip_box, 1)
        row.addWidget(self.loop)
        row.addWidget(self.speed)
        row.addWidget(self.replay)
        lay.addLayout(row)
        view_row = QHBoxLayout()
        self.pan = QPushButton("Pan")
        self.pan.setCheckable(True)
        self.pan.setToolTip("Drag to slide the view instead of turning it. Right-drag pans "
                            "at any time.")
        self.pan.toggled.connect(lambda on: self.view.page().runJavaScript(
            f"window.preview && window.preview.setPanMode({'true' if on else 'false'})"))
        self.reset_view = QPushButton("Reset view")
        self.reset_view.setToolTip("Show the whole creature again.")
        self.reset_view.clicked.connect(lambda: self.view.page().runJavaScript(
            "window.preview && window.preview.resetView()"))
        view_row.addWidget(self.pan)
        view_row.addWidget(self.reset_view)
        hint = QLabel("<small>Right-drag pans. Double-click a part to centre on it. Scroll "
                      "zooms toward the mouse.</small>")
        hint.setWordWrap(True)
        view_row.addWidget(hint, 1)
        lay.addLayout(view_row)
        self.status = QLabel("")
        lay.addWidget(self.status)
        # The page has to be up before a model can be sent to it; a cached model is ready
        # at once, before three.js has even parsed, so loads wait for this signal.
        self._page_ready = False
        self._queued: Path | None = None
        self.view.loadFinished.connect(self._page_loaded)
        self.view.load(QUrl(self.server.url("creature.html")))
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._check_loaded)

    # -- loading ------------------------------------------------------------------

    def show_model(self, ref: str):
        self.ref = ref
        self.clips = []
        self.clip_box.blockSignals(True)
        self.clip_box.clear()
        self.clip_box.blockSignals(False)
        try:
            path = self.cache.cached(ref)
        except Exception as e:
            self.status.setText(f"Can't show this model: {e}")
            return
        if path is not None:
            self._load(path)
            return
        self.status.setText("Preparing the 3D preview (about six seconds, once per model)...")
        t = ExportThread(self.cache, ref)
        t.done.connect(self._exported)
        t.finished.connect(lambda t=t: self._threads.discard(t))
        self._threads.add(t)
        t.start()

    def _exported(self, ref: str, path: str, error: str):
        if ref != self.ref:
            return                         # the person moved on to another creature
        if error:
            self.status.setText(f"Can't show this model: {error}")
            return
        self._load(Path(path))

    def _page_loaded(self, ok: bool):
        self._page_ready = ok
        if not ok:
            self.status.setText("The preview page did not load.")
        elif self._queued is not None:
            path, self._queued = self._queued, None
            self._load(path)

    def _load(self, path: Path):
        self._want = path
        if not self._page_ready:
            self._queued = path
            self.status.setText("Starting the preview...")
            return
        rel = path.relative_to(self.cache.root).as_posix()
        self.status.setText("Loading...")
        self.view.page().runJavaScript(
            f"window.preview && window.preview.load({json.dumps('/cache/' + rel)})")
        self._pending = 240                # up to a minute for the largest models
        self._poll.start()

    def _check_loaded(self):
        def got(state):
            if not state:
                # the page itself may still be starting: retry the load once it is up
                self._pending -= 1
                if self._pending <= 0:
                    self._poll.stop()
                    self.status.setText("The preview did not start.")
                elif self._pending % 4 == 0:
                    rel = self._want.relative_to(self.cache.root).as_posix()
                    self.view.page().runJavaScript(
                        f"window.preview && window.preview.load({json.dumps('/cache/' + rel)})")
                return
            s = json.loads(state)
            if s.get("error"):
                self._poll.stop()
                self.status.setText(f"Can't show this model: {s['error']}")
            elif s.get("loaded"):
                self._poll.stop()
                self.clips = [c["name"] for c in s.get("clips", [])]
                self.clip_box.blockSignals(True)
                self.clip_box.clear()
                self.clip_box.addItems(self.clips)
                idle = next((c for c in self.clips if c.lower().startswith("idle")), None)
                if idle:
                    self.clip_box.setCurrentText(idle)
                self.clip_box.blockSignals(False)
                self.status.setText(f"{len(self.clips)} animations. Drag to turn, scroll to zoom.")
                self._play_current(self.clip_box.currentText())
                self.clips_changed.emit(self.clips)
            else:
                self._pending -= 1
                if self._pending <= 0:
                    self._poll.stop()
                    self.status.setText("The preview took too long to load.")
        self.view.page().runJavaScript(
            "window.preview ? JSON.stringify(window.preview.state()) : null", 0, got)

    def _play_current(self, name: str):
        if not name:
            return
        speed = float(self.speed.currentText().rstrip("x"))
        self.view.page().runJavaScript(
            f"window.preview && window.preview.play({json.dumps(name)}, "
            f"{'true' if self.loop.isChecked() else 'false'}, {speed})")

    def play(self, name: str):
        """Pick and play a clip, e.g. from the Model card's list."""
        if name in self.clips:
            self.clip_box.setCurrentText(name)
            self._play_current(name)

    def wait_threads(self):
        for t in list(self._threads):
            t.wait()
