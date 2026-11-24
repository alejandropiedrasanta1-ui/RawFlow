// RawFlow · paso 4 sin ventanas: retoca con el MOTOR de Retouch4me (el mismo que usa Arams por dentro).
// Da el mismo resultado que exportar desde Arams (comprobado píxel a píxel), pero sin abrir su ventana,
// sin crear proyectos, sin generar vistas previas y sin OCR ni clics.
//
// Se ejecuta con el propio Arams en modo Node:
//   ELECTRON_RUN_AS_NODE=1 "/Applications/Retouch4me Arams.app/Contents/MacOS/Retouch4me Arams" r4m_worker.js trabajo.json
// trabajo.json = {
//   "logDir": "...",                               carpeta (ya creada) para el registro del motor
//   "stopFile": "...",                             si aparece, se interrumpe la foto en curso y se sale
//   "plugins": [{"id","path","User Params"}, ...], cadena de plugins (como la arma Arams con el preajuste)
//   "quality": 100,
//   "items": [{"in": "...jpg", "tmp": "...", "out": "...jpg"}, ...]
// }
// Salida (una línea por evento, la lee rawflow/retouch_engine.py):
//   READY <s>  ·  LOADERR <id> <código> <texto>  ·  START <i>  ·  PROG <i> <0..1>
//   OK <i> <s>  ·  ERR <i> <código> <texto>  ·  END
'use strict';
const fs = require('fs');
const path = require('path');

// Mac: …/Contents/MacOS/<exe> → …/Contents/Resources · Windows: <carpeta>\<exe> → <carpeta>\resources
const ENGINE = [path.resolve(process.execPath, '../../Resources'), path.resolve(process.execPath, '../resources')]
  .flatMap(RES => [
    path.join(RES, 'app-arm64.asar/node_modules/@retouch4me/engine'),
    path.join(RES, 'app-x64.asar/node_modules/@retouch4me/engine'),
    path.join(RES, 'app.asar/node_modules/@retouch4me/engine'),
  ]).find(p => { try { fs.statSync(path.join(p, 'package.json')); return true; } catch (e) { return false; } });

// Los avisos van por un canal propio (RF_FD) y con escritura SÍNCRONA: el motor bloquea el hilo de Node
// mientras retoca, así que process.stdout (asíncrono en macOS) no saldría hasta el final. La salida suelta
// del motor (stdout/stderr) va a un archivo de registro y no se mezcla con estos avisos.
// En Windows no se heredan descriptores sueltos: los avisos se añaden a un archivo (RF_FILE) que RawFlow va leyendo.
const OUT = process.env.RF_FILE ? fs.openSync(process.env.RF_FILE, 'a') : parseInt(process.env.RF_FD || '1', 10);
function say(...a) {
  const buf = Buffer.from(a.join(' ') + '\n', 'utf8');
  for (let off = 0, tries = 0; off < buf.length && tries < 2000;) {
    try { off += fs.writeSync(OUT, buf, off); } catch (e) {
      if (e.code !== 'EAGAIN') return;
      tries++; const t = Date.now(); while (Date.now() - t < 5) { /* espera corta */ }
    }
  }
}
function clean(s) { return String(s || '').replace(/\s+/g, ' ').slice(0, 300); }

if (!ENGINE) { say('FATAL 1 No encuentro el motor de Retouch4me dentro de Arams'); process.exit(3); }
const { EngineWrapper, parseErrorCode } = require(ENGINE);
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const t0 = Date.now();
const secs = since => ((Date.now() - since) / 1000).toFixed(2);

try {
  fs.mkdirSync(job.logDir, { recursive: true });
  EngineWrapper.setLogFolder({ logFolder: job.logDir, isPrimary: true });
} catch (e) { /* sin registro del motor: no es grave */ }

const eng = EngineWrapper.getInstance();
const loadErrors = [];
const rc = eng.loadPlugins({ plugins: job.plugins.map(p => ({ id: p.id, path: p.path })) }, ev => {
  if (ev && ev.code !== undefined && ev.code !== 0) {
    const p = job.plugins[ev.pluginIndex] || {};
    loadErrors.push(p.id);
    say('LOADERR', JSON.stringify(p.id || '?'), ev.code, clean(parseErrorCode(ev.code)));
  }
});
const rcCode = typeof rc === 'object' && rc !== null ? rc.code : rc;
if (rcCode !== 0 || loadErrors.length) {
  say('FATAL', rcCode, clean(parseErrorCode(rcCode)));
  process.exit(2);
}
say('READY', secs(t0));

let stopped = false;
const PARENT = process.ppid;                 // si RawFlow se cierra, el motor no sigue trabajando solo
// (en Windows el «padre» de un proceso no cambia aunque muera: hay que preguntar si sigue vivo; sin esto, si RawFlow
// se cerraba de golpe, el motor seguía horas con la tarjeta gráfica)
const parentAlive = () => { try { process.kill(PARENT, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
const stopNow = () => {
  if (process.ppid !== PARENT || !parentAlive()) return true;
  try { return !!job.stopFile && fs.existsSync(job.stopFile); } catch (e) { return false; }
};

for (let i = 0; i < job.items.length; i++) {
  if (stopNow()) { stopped = true; break; }
  const it = job.items[i];
  const ts = Date.now();
  let lastProg = 0;
  say('START', i);
  let r;
  try {
    r = eng.applyPlugins({
      inputImagePath: it.in,
      outputImagePath: it.tmp,
      plugins: job.plugins,
      // lo mismo que pone Arams al exportar una foto recién importada (valoración 0, sin etiqueta)
      tags: [], rating: 0, autoRating: 0, colorLabel: '',
      exportJpegQuality: job.quality || 100,
      overrideFormat: 'jpeg',
      resizeOptions: { resizeMode: 0, resizeEdgeSize: 0, enlargeDuringResize: 0 },
      calcResize: (w, h) => ({ resizeWidth: w, resizeHeight: h }),
      forceRaw: false,
      forceFullSize: true,
    }, ev => {
      if (stopNow()) { stopped = true; eng.interruptRun(); return; }
      const f = ev && typeof ev.progressOfTotal === 'number' ? ev.progressOfTotal : null;
      const now = Date.now();
      if (f !== null && now - lastProg > 1000) { lastProg = now; say('PROG', i, f.toFixed(3)); }
    });
  } catch (e) {
    r = { code: -1, errorDescription: clean(e && e.message) };
  }
  const code = r && typeof r.code === 'number' ? r.code : -1;
  if (stopped) break;
  if (code === 0) {
    try {
      fs.renameSync(it.tmp, it.out);                 // la foto final aparece entera, de golpe
      say('OK', i, secs(ts));
    } catch (e) {
      say('ERR', i, 27, clean('No pude guardar ' + it.out + ': ' + (e && e.message)));
    }
  } else {
    try { fs.unlinkSync(it.tmp); } catch (e) { /* no existía */ }
    say('ERR', i, code, clean((r && r.errorDescription) || parseErrorCode(code)));
    if ([1, 10, 16, 21, 22, 23, 31, 32, 36].includes(code)) process.exit(4);   // plugin/registro: no sigue
  }
}
say(stopped ? 'STOPPED' : 'END', secs(t0));
process.exit(0);
