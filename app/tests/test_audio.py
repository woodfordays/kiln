import contextlib
import json
import unittest
import unittest.mock

import httpx

from kiln import audiocpp, config, db, fit, multipart, proxy, roles
from kiln.route import resolve
from kiln.server import Fleet, manager
from types import SimpleNamespace as NS
from . import ROOT
from .helpers import fake_gpu, llm, make_preset


def row(flag, value=None, enabled=True):
    return {"flag": flag, "value": value, "enabled": enabled}


def audio_model(name):
    p = ROOT / "models" / f"{name}.gguf"
    p.write_bytes(b"abcd" * 16)
    return p


class ConfigForTests(unittest.TestCase):
    def test_rows_map_into_the_config(self):
        rows = [row("--family", "kokoro_tts"), row("--task", "tts"), row("--mode", "streaming"),
                row("--device", "1"), row("--threads", "6"), row("--voice", "alba"), row("--backend", "cuda"),
                row("--load-option", "a=1"), row("--load-option", "b=x=y"), row("--session-option", "s=2"),
                row("--request-option", "r=3")]
        conf, extra = audiocpp.config_for("kok", "/m/k.gguf", rows, "tts", 4321)
        self.assertEqual(extra, [])
        self.assertEqual((conf["host"], conf["port"], conf["backend"], conf["threads"]), ("127.0.0.1", 4321, "cuda", 6))
        m, = conf["models"]
        self.assertEqual((m["id"], m["path"], m["family"], m["task"], m["mode"], m["device"]),
                         ("kok", "/m/k.gguf", "kokoro_tts", "tts", "streaming", 1))
        self.assertEqual(m["default_voice_preset"], {"voice_id": "alba"})
        self.assertEqual(m["load_options"], {"a": "1", "b": "x=y"})
        self.assertEqual(m["session_options"], {"s": "2"})
        self.assertEqual(m["default_request_options"], {"r": "3"})

    def test_task_defaults_follow_the_role(self):
        self.assertEqual(audiocpp.config_for("a", "/m", [row("--family", "f")], "tts", 1)[0]["models"][0]["task"], "tts")
        self.assertEqual(audiocpp.config_for("a", "/m", [row("--family", "f")], "stt", 1)[0]["models"][0]["task"], "asr")

    def test_managed_flags_are_ignored_and_unknown_ones_pass_through(self):
        rows = [row("--family", "f"), row("--config", "x.json"), row("--port", "9"), row("--host", "h"),
                row("--no-ui"), row("-m", "z"), row("--fancy", "1"), row("--flagonly"), row("--off", "1", enabled=False)]
        conf, extra = audiocpp.config_for("a", "/m", rows, "stt", 77)
        self.assertEqual(extra, ["--fancy", "1", "--flagonly"])
        self.assertEqual(conf["port"], 77)

    def test_family_is_required_and_options_need_key_value(self):
        with self.assertRaises(ValueError):
            audiocpp.config_for("a", "/m", [row("--voice", "x")], "tts", 1)
        with self.assertRaises(ValueError):
            audiocpp.config_for("a", "/m", [row("--family", "f"), row("--load-option", "novalue")], "tts", 1)


class FitAndRolesTests(unittest.TestCase):
    def test_apply_needs_a_family(self):
        with self.assertRaises(ValueError):
            roles.apply("tts", "/m/k.gguf", [])
        rows, notes = roles.apply("stt", "/m/k.gguf", [row("--family", "qwen3_asr")])
        self.assertEqual(rows, [row("--family", "qwen3_asr")])

    def test_cpu_backend_uses_no_gpu_memory(self):
        p = audio_model("fitcpu")
        est = fit.estimate_audio(str(p), [row("--backend", "cpu")])
        self.assertEqual(est["gpu_mib"], 0)
        self.assertEqual(fit.estimate(str(p), [row("--backend", "cpu")], "tts")["gpu_mib"], 0)
        self.assertGreater(fit.estimate_audio(str(p), [row("--backend", "cuda")])["gpu_mib"], 0)

    def test_directory_size_is_summed(self):
        d = ROOT / "models" / "pkg"
        (d / "sub").mkdir(parents=True, exist_ok=True)
        (d / "a.bin").write_bytes(b"x" * 1000)
        (d / "sub" / "b.bin").write_bytes(b"y" * 500)
        self.assertEqual(fit.file_size(str(d)), 1500)


class MultipartTests(unittest.TestCase):
    CT = "multipart/form-data; boundary=XX"
    BIN = bytes(range(256)) + b"\r\n--XX\r\nnot a delimiter"

    def body(self, eol=b"\r\n", with_model=True):
        out = b"--XX" + eol + b'Content-Disposition: form-data; name="file"; filename="a.wav"' + eol \
              + b"Content-Type: audio/wav" + eol + eol + self.BIN + eol
        if with_model:
            out += b"--XX" + eol + b'Content-Disposition: form-data; name="model"' + eol + eol + b"whisper-1" + eol
        return out + b"--XX--" + eol

    def test_field_reads_and_ignores_files(self):
        self.assertEqual(multipart.field(self.body(), self.CT, "model"), "whisper-1")
        self.assertIsNone(multipart.field(self.body(), self.CT, "file"))
        self.assertIsNone(multipart.field(self.body(with_model=False), self.CT, "model"))
        self.assertIsNone(multipart.field(self.body(), "application/json", "model"))

    def test_set_field_replaces_and_keeps_the_file_bytes(self):
        out = multipart.set_field(self.body(), self.CT, "model", "kok")
        self.assertEqual(multipart.field(out, self.CT, "model"), "kok")
        self.assertIn(self.BIN, out)
        self.assertEqual(out, self.body().replace(b"whisper-1", b"kok"))

    def test_set_field_inserts_when_missing(self):
        out = multipart.set_field(self.body(with_model=False), self.CT, "model", "kok")
        self.assertEqual(multipart.field(out, self.CT, "model"), "kok")
        self.assertIn(self.BIN, out)
        self.assertTrue(out.endswith(b"--XX--\r\n"))

    def test_quoted_boundary_and_lf_only_bodies(self):
        self.assertEqual(multipart.boundary('multipart/form-data; boundary="XX"'), b"XX")
        ct = 'multipart/form-data; boundary="XX"'
        binary = bytes(range(256))
        lf = (b"--XX\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\n\n" + binary
              + b"\n--XX\nContent-Disposition: form-data; name=\"model\"\n\nwhisper-1\n--XX--\n")
        self.assertEqual(multipart.field(lf, ct, "model"), "whisper-1")
        out = multipart.set_field(lf, ct, "model", "k")
        self.assertEqual(multipart.field(out, ct, "model"), "k")
        self.assertEqual(out, lf.replace(b"whisper-1", b"k"))
        self.assertIn(binary, out)


def inst(name, role="chat", run_id=1):
    return NS(name=name, role=role, state="ready", last_used=0.0, model_name=name, model_path=f"/m/{name}.gguf",
              draining=False, run_id=run_id, aliases=lambda: [name])


class AudioRouteTests(unittest.TestCase):
    def test_speech_picks_the_tts_instance(self):
        chat, tts = inst("C", run_id=1), inst("kok", "tts", 2)
        r = resolve(None, "/v1/audio/speech", [chat, tts], [], False)
        self.assertIs(r.instance, tts)
        self.assertIs(resolve("tts-1", "/v1/audio/speech", [chat, tts], [], True).instance, tts)
        stt = inst("w", "stt", 3)
        self.assertIs(resolve("whisper-1", "/v1/audio/transcriptions", [chat, tts, stt], [], False).instance, stt)

    def test_no_audio_model_is_not_answered_by_chat(self):
        chat = inst("C")
        r = resolve("tts-1", "/v1/audio/speech", [chat], [], True)
        self.assertEqual((r.kind, r.reason), ("none", "No text-to-speech model is loaded."))
        r = resolve(None, "/v1/audio/transcriptions", [chat], [], False)
        self.assertEqual((r.kind, r.reason), ("none", "No speech-to-text model is loaded."))


FAMILY = [{"flag": "--family", "value": "kokoro_tts"}, {"flag": "--backend", "value": "cpu"}]
ASR = [{"flag": "--family", "value": "qwen3_asr"}, {"flag": "--backend", "value": "cpu"}]


class AudioFleetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.execute("DELETE FROM presets")
        db.set_settings({"max_instances": 4, "idle_timeout_min": 10})
        self.addCleanup(db.set_settings, {"idle_timeout_min": 120})
        self.fleet = Fleet()

    async def asyncTearDown(self):
        await self.fleet.shutdown()

    async def test_a_tts_preset_runs_on_audiocpp(self):
        log = ROOT / "fake-audio.log"
        log.unlink(missing_ok=True)
        p = make_preset("Kokoro", audio_model("kokoro"), role="tts", alias="kok", flags=FAMILY,
                        env={"FAKE_LOG": str(log)})
        inst = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, role="tts")
        await inst.wait_ready(10)
        self.assertEqual(inst.engine, "audiocpp")
        self.assertIn("--config", inst.argv)
        self.assertIn("--no-ui", inst.argv)
        for bad in ("--alias", "--metrics"):
            self.assertNotIn(bad, inst.argv)
        seen = json.loads(log.read_text().splitlines()[0])
        m, = seen["config"]["models"]
        self.assertEqual((m["id"], m["family"], m["task"]), ("kok", "kokoro_tts", "tts"))
        self.assertEqual(seen["config"]["port"], inst.port)
        st = next(i for i in self.fleet.status()["instances"] if i["name"] == "kok")
        self.assertEqual(st["engine"], "audiocpp")

    async def test_library_path_starts_with_the_audiocpp_dir(self):
        log = ROOT / "fake-audio-env.log"
        log.unlink(missing_ok=True)
        p = make_preset("Whisper", audio_model("whisper"), role="stt", flags=ASR, env={"FAKE_LOG": str(log)})
        inst = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, role="stt")
        await inst.wait_ready(10)
        # the fake does not log its environment, so check the one Kiln built for it
        self.assertTrue(inst.argv[0].endswith("fake_audiocpp.py"))
        self.assertEqual(json.loads(log.read_text().splitlines()[0])["config"]["models"][0]["id"], "Whisper")
        import os
        with open(f"/proc/{inst.proc.pid}/environ", "rb") as f:
            env = dict(l.split(b"=", 1) for l in f.read().split(b"\0") if b"=" in l)
        self.assertTrue(env[b"LD_LIBRARY_PATH"].decode().startswith(config.AUDIOCPP_LIB_DIR + ":"))


class AudioProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await manager.stop()
        manager.blocked = None
        db.execute("DELETE FROM presets")
        db.execute("DELETE FROM requests")
        db.set_settings({"swap_on_request": True, "max_instances": 4, "idle_timeout_min": 10})
        self.addCleanup(db.set_settings, {"idle_timeout_min": 120})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url="http://kiln", timeout=60)
        self.upstream, proxy._client = proxy._client, httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))

    async def asyncTearDown(self):
        await self.http.aclose()
        await manager.stop()
        await proxy._client.aclose()
        proxy._client = self.upstream

    async def up(self, name, path=None, **kw):
        p = make_preset(name, path or audio_model(name), **kw)
        inst = await manager.start(p["model"], p["flags"], p["env"], preset=p, role=p["role"])
        await inst.wait_ready(10)
        inst.ready_at -= 10
        return inst

    def speak(self, model=None, text="hello"):
        body = {"input": text, "voice": "alba"}
        if model:
            body["model"] = model
        return self.http.post("/v1/audio/speech", json=body)

    async def test_speech_by_alias_and_by_openai_name(self):
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        r = await self.speak("kok")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "audio/wav")
        self.assertEqual(r.headers["x-kiln-model"], "kok")
        self.assertEqual(r.content[:4], b"RIFF")
        self.assertEqual(len(r.content), 44 + 2 * len("hello"))
        r = await self.speak("tts-1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["x-kiln-model"], "kok")
        r = await self.speak()
        self.assertEqual(r.status_code, 200)

    async def test_transcription_multipart(self):
        await self.up("Whisper", role="stt", alias="asr", flags=ASR)
        audio = bytes(range(200))
        r = await self.http.post("/v1/audio/transcriptions", data={"model": "whisper-1"},
                                 files={"file": ("a.wav", audio, "audio/wav")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["text"], "heard 200 bytes")
        self.assertEqual(r.headers["x-kiln-model"], "asr")
        r = await self.http.post("/v1/audio/transcriptions", files={"file": ("a.wav", audio, "audio/wav")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["text"], "heard 200 bytes")

    async def test_voices(self):
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        r = await self.http.get("/v1/audio/voices", params={"model": "kok"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"voices": ["alba", "cosette"]})

    async def test_no_tts_model_says_so(self):
        self.stack.enter_context(unittest.mock.patch.object(proxy, "get_preset", proxy.get_preset))
        await self.up("chatty", path=llm(ROOT / "models" / "chatty.gguf"))
        r = await self.speak("tts-1")
        self.assertEqual(r.status_code, 503)
        self.assertIn("No text-to-speech model is loaded.", r.text)

    async def test_swap_on_request_loads_a_tts_preset(self):
        make_preset("Kokoro", audio_model("kokoro2"), role="tts", alias="kok2", flags=FAMILY)
        r = await self.speak("kok2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["x-kiln-model"], "kok2")
        self.assertEqual([i.engine for i in manager.live()], ["audiocpp"])

    async def test_chat_without_a_model_still_reaches_the_chat_model(self):
        await self.up("chatty", path=llm(ROOT / "models" / "chatty.gguf"))
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        r = await self.http.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["x-kiln-model"], "chatty")

    async def test_the_request_is_recorded(self):
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        await self.speak("kok")
        rows = db.rows("SELECT * FROM requests ORDER BY id")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["endpoint"], rows[0]["status"], rows[0]["model"]), ("/v1/audio/speech", 200, "Kokoro"))

    async def test_voices_without_a_model_reach_the_tts_model(self):
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        r = await self.http.get("/v1/audio/voices")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"voices": ["alba", "cosette"]})

    async def test_preflight_is_answered_by_kiln(self):
        r = await self.http.options("/v1/audio/speech", headers={
            "Origin": "http://x", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
        self.assertIn("content-type", r.headers["access-control-allow-headers"].lower())

    async def test_audio_responses_allow_any_origin(self):
        await self.up("Kokoro", role="tts", alias="kok", flags=FAMILY)
        r = await self.speak("kok")
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
