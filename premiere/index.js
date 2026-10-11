// Smooth Cursor для Premiere Pro: раз в секунду спрашивает у программы задание и кладёт готовое видео в открытую
// секвенцию — на плейхед, а если там занято, то в первую дыру после него, куда клип влезает целиком (V1 и A1).
// Программа не запущена — запрос просто не проходит, плагин молчит.
const ppro = require("premierepro");
const APP = "http://localhost:47913";

const norm = (p) => p.replace(/\//g, "\\").toLowerCase();

async function findItem(folder, path) {
  // только что импортированный файл: последний в папке с тем же путём (импорт повторный — дубли)
  let found = null;
  for (const it of await folder.getItems()) {
    try {
      if (norm(await ppro.ClipProjectItem.cast(it).getMediaFilePath()) === norm(path)) found = it;
    } catch (e) { /* папка или не клип */ }
  }
  return found;
}

async function spans(track) {
  // [начало, конец] клипов дорожки в секундах
  if (!track) return [];
  const out = [];
  for (const it of await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false)) {
    out.push([(await it.getStartTime()).seconds, (await it.getEndTime()).seconds]);
  }
  return out;
}

async function testSequence(project, name, item, bin) {
  // для самопроверки (job.sequence): своя секвенция вместо открытой; нет — создать из этого же видео
  for (const s of await project.getSequences()) if (s.name === name) return s;
  return await project.createSequenceFromMedia(name, [ppro.ClipProjectItem.cast(item)], bin);
}

async function place(job) {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("no open project");
  const bin = await project.getRootItem();
  if (!(await project.importFiles([job.path], true, bin, false))) throw new Error("import failed: " + job.path);
  const item = await findItem(bin, job.path);
  if (!item) throw new Error("imported item not found: " + job.path);
  const seq = job.sequence ? await testSequence(project, job.sequence, item, bin) : await project.getActiveSequence();
  if (!seq) throw new Error("no open sequence");

  const eps = 1e-4;
  const busy = [...(await spans(await seq.getVideoTrack(0))), ...(await spans(await seq.getAudioTrack(0)))];
  let t = (await seq.getPlayerPosition()).seconds;
  for (let moved = true; moved; ) {  // занято — сразу за мешающим клипом, и снова проверить
    moved = false;
    for (const [a, b] of busy) {
      if (a < t + job.duration - eps && b > t + eps) {
        t = b;
        moved = true;
      }
    }
  }
  const editor = ppro.SequenceEditor.getEditor(seq);
  let ok = false;
  project.lockedAccess(() => {
    ok = project.executeTransaction((compound) => {
      compound.addAction(editor.createOverwriteItemAction(item, ppro.TickTime.createWithSeconds(t), 0, 0));
    }, "Smooth Cursor");
  });
  if (!ok) throw new Error("Premiere did not place the clip");
  return {at: t, sequence: seq.name};
}

async function poll() {
  try {
    const r = await fetch(APP + "/job");
    if (r.status === 200) {
      const job = await r.json();
      let res;
      try {
        res = {id: job.id, ok: true, ...(await place(job))};
      } catch (e) {
        res = {id: job.id, ok: false, error: String((e && e.message) || e)};
      }
      await fetch(APP + "/done", {method: "POST", headers: {"Content-Type": "application/json"},
                                  body: JSON.stringify(res)});
    }
  } catch (e) { /* программа не запущена */ }
  setTimeout(poll, 1000);
}

poll();
