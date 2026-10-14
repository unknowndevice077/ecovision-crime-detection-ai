"""Alarm verifier backends (maincode/alarm_verifier.py): which backend loads,
which cut-off applies, and how llama-server's answer becomes a score. No
model or server is started -- loading and the HTTP call are stubbed. The
shipped cpu backend's scores were checked against the experiment's in the
training repo (experiments/crop_recheck/verify_cpu_backend_shipped.py)."""
import os
import sys
import threading
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maincode"))
import alarm_verifier as AV  # noqa: E402

TASKS = {"violence": {"stage1_threshold": 0.25, "stage1_consecutive": 1, "cut": -5.0, "cut_cpu": -6.0},
         "robbery": {"stage1_threshold": 0.6, "stage1_consecutive": 2, "cut": -6.0}}

NO_GUARD = {"physical_gb": 0, "commit_gb": 0}


def verifier(backend, gpu_why=None):
    v = AV.AlarmVerifier({"enabled": False, "backend": backend, "tasks": TASKS,
                          "load_delay_seconds": 0, "reason": False,
                          "ram_guard": {"gpu": NO_GUARD, "cpu": NO_GUARD}}, ".")
    v._gpu_unavailable = lambda: gpu_why
    v._load_gpu = lambda: None
    return v


class _FakeLlama:
    def __init__(self, cfg, root):
        self.model, self.threads = "fake.gguf", 2
        self.scores = []

    def start(self):
        pass

    def stop(self):
        pass

    def score(self, question, clip):
        return self.scores.pop(0)


class BackendChoice(unittest.TestCase):
    def setUp(self):
        self._orig = AV.LlamaBackend
        AV.LlamaBackend = _FakeLlama

    def tearDown(self):
        AV.LlamaBackend = self._orig

    def test_auto_uses_gpu_when_it_fits(self):
        v = verifier("auto")
        v._load()
        self.assertEqual(v.active_backend, "gpu")

    def test_auto_falls_back_to_cpu(self):
        v = verifier("auto", gpu_why="only 1.0 GB free")
        v._load()
        self.assertEqual(v.active_backend, "cpu")

    def test_gpu_only_refuses_instead_of_using_cpu(self):
        v = verifier("gpu", gpu_why="only 1.0 GB free")
        with self.assertRaises(RuntimeError):
            v._load()
        self.assertIsNone(v.active_backend)

    def test_cpu_never_touches_the_gpu(self):
        v = verifier("cpu")
        v._gpu_unavailable = lambda: self.fail("cpu backend checked the GPU")
        v._load()
        self.assertEqual(v.active_backend, "cpu")


class MemoryGuard(unittest.TestCase):
    """Per backend, on physical RAM AND commit headroom (measured needs:
    gpu +1.3 GB physical / +5.3 GB committed, cpu ~2.1 GB of both)."""

    def short(self, backend, phys, commit, cfg=None):
        v = AV.AlarmVerifier(dict({"enabled": False}, **(cfg or {})), ".")
        v._memory_free = lambda: (phys, commit)
        return v._memory_short(backend)

    def test_gpu_is_limited_by_commit_not_physical(self):
        self.assertIsNone(self.short("gpu", 2.0, 8.0))
        self.assertIn("commit", self.short("gpu", 8.0, 4.0))

    def test_cpu_needs_resident_ram(self):
        self.assertIsNone(self.short("cpu", 3.0, 3.0))
        self.assertIn("RAM", self.short("cpu", 2.0, 30.0))

    def test_unknown_values_do_not_block(self):
        self.assertIsNone(self.short("gpu", None, None))

    def test_config_override(self):
        self.assertIsNone(self.short("cpu", 1.0, 1.0, {"ram_guard": {"cpu": {"physical_gb": 0.5, "commit_gb": 0.5}}}))

    def test_auto_falls_back_to_cpu_when_commit_is_short(self):
        orig = AV.LlamaBackend
        AV.LlamaBackend = _FakeLlama
        try:
            v = AV.AlarmVerifier({"enabled": False, "backend": "auto", "load_delay_seconds": 0, "tasks": TASKS}, ".")
            v._gpu_unavailable = lambda: None
            v._memory_free = lambda: (4.0, 4.0)       # enough for cpu, not for gpu
            v._load()
            self.assertEqual(v.active_backend, "cpu")
        finally:
            AV.LlamaBackend = orig


class Cuts(unittest.TestCase):
    def test_each_backend_has_its_own_cut(self):
        v = verifier("gpu")
        v.active_backend = "gpu"
        self.assertEqual(v.cut("violence"), -5.0)
        v.active_backend = "cpu"
        self.assertEqual(v.cut("violence"), -6.0)

    def test_task_without_a_cpu_cut_falls_back_to_strict(self):
        """robbery has no cut_cpu: the check reports no verdict (None), which
        VerifiedGate turns into the detector's strict setting -- never a
        verdict against the gpu's cut on the cpu's scale."""
        orig = AV.LlamaBackend
        AV.LlamaBackend = _FakeLlama
        try:
            v = verifier("cpu")
            v.enabled = True
            v.max_queue = 4
            threading.Thread(target=v._worker, daemon=True).start()
            for _ in range(200):
                if v.active:
                    break
                threading.Event().wait(0.01)
            v._llama.scores = [-5.5, -5.5]
            v.history.push(np.zeros((72, 128, 3), np.uint8), 0.0)
            got = []
            done = threading.Event()

            def cb(passed, info, ctx):
                got.append(passed)
                if len(got) == 2:
                    done.set()
            self.assertTrue(v.submit("violence", cb, end_ts=0.0))
            self.assertTrue(v.submit("robbery", cb, end_ts=0.0))
            self.assertTrue(done.wait(5))
            self.assertEqual(got, [True, None])   # -5.5 >= cut_cpu -6.0; robbery: no cpu cut
        finally:
            AV.LlamaBackend = orig


class _Det:
    candidate_threshold = 0.25
    candidate_started = False
    candidate_confirmed = False


class _InstantVerifier:
    """Answers every submit at once with a fixed verdict per task."""

    def __init__(self, verdicts, active=True):
        self.verdicts, self.active, self.submitted = verdicts, active, []

    def submit(self, task, on_done, end_ts=None, context=None):
        self.submitted.append(task)
        on_done(self.verdicts[task], {"score": 0.0}, context)
        return True


class RobberyFromFight(unittest.TestCase):
    """main.py steps a gate with task robbery_from_fight on the FIGHT
    detector's whole-frame candidates, with strict_hit=False, and ORs it into
    the robbery signal. (One shared candidate stream here; in the app the
    two gates read two candidate states of the same detector.)"""

    def run_episode(self, v):
        det = _Det()
        fight = AV.VerifiedGate("violence", v)
        rob = AV.VerifiedGate("robbery_from_fight", v)
        out = []
        for i, on in enumerate([False, True, True, False]):
            det.candidate_started = on and not det.candidate_confirmed
            det.candidate_confirmed = on
            out.append((fight.step(det, False, float(i)), rob.step(det, False, float(i))))
        return out

    def test_one_candidate_asks_both_questions(self):
        v = _InstantVerifier({"violence": False, "robbery_from_fight": True})
        out = self.run_episode(v)
        self.assertEqual(v.submitted, ["violence", "robbery_from_fight"])
        self.assertEqual([r for _, r in out], [False, True, True, True])   # held min_hold after the candidate
        self.assertFalse(any(f for f, _ in out))

    def test_rejected_or_unanswered_never_alarms(self):
        for verdict in (False, None):
            v = _InstantVerifier({"violence": False, "robbery_from_fight": verdict})
            self.assertFalse(any(r for _, r in self.run_episode(v)))

    def test_verifier_off_means_the_path_does_not_exist(self):
        v = _InstantVerifier({"violence": True, "robbery_from_fight": True}, active=False)
        self.assertFalse(any(r for _, r in self.run_episode(v)))
        self.assertEqual(v.submitted, [])


class _DeferredVerifier:
    """Holds every submitted check until the test answers it."""
    active = True

    def __init__(self):
        self.calls = []                # (end_ts, on_done, context)

    def submit(self, task, on_done, end_ts=None, context=None):
        self.calls.append((end_ts, on_done, context))
        return True

    def answer(self, i, passed):
        _, on_done, ctx = self.calls[i]
        on_done(passed, {"score": 0.0}, ctx)


class Recheck(unittest.TestCase):
    def setUp(self):
        self.det = _Det()
        self.v = _DeferredVerifier()

    def step(self, gate, t, on=True, frame_id=None):
        self.det.candidate_started = on and not self.det.candidate_confirmed
        self.det.candidate_confirmed = on
        frame = None if frame_id is None else np.full((2, 2, 3), frame_id, np.uint8)
        return gate.step(self.det, False, t, frame, 0.5)

    def test_rechecks_a_long_rejected_candidate(self):
        g = AV.VerifiedGate("violence", self.v, recheck=7.0)
        self.step(g, 0.0, frame_id=1)
        self.v.answer(0, False)
        self.assertFalse(self.step(g, 3.0))
        self.step(g, 6.9)
        self.assertEqual(len(self.v.calls), 1)          # not yet
        self.step(g, 7.0, frame_id=2)
        self.assertEqual(len(self.v.calls), 2)          # re-check
        self.v.answer(1, True)
        self.assertTrue(self.step(g, 8.0))
        self.assertEqual(int(g.alert_extras()["snap_src"][0, 0, 0]), 2)   # the frame of the check that passed

    def test_never_while_a_check_is_pending(self):
        g = AV.VerifiedGate("violence", self.v, recheck=7.0)
        self.step(g, 0.0)
        for t in (7.0, 14.0, 21.0):
            self.step(g, t)
        self.assertEqual(len(self.v.calls), 1)

    def test_off_means_one_check_per_episode(self):
        g = AV.VerifiedGate("violence", self.v)
        self.step(g, 0.0)
        self.v.answer(0, False)
        for t in (7.0, 14.0, 21.0):
            self.step(g, t)
        self.assertEqual(len(self.v.calls), 1)

    def test_no_recheck_after_a_pass_or_a_fallback(self):
        g = AV.VerifiedGate("violence", self.v, recheck=7.0)
        self.step(g, 0.0)
        self.v.answer(0, True)
        for t in (1.0, 8.0, 15.0):
            self.assertTrue(self.step(g, t))
        self.assertEqual(len(self.v.calls), 1)
        g2 = AV.VerifiedGate("violence", _DeferredVerifier(), recheck=7.0)
        self.det = _Det()
        self.v = g2.verifier
        self.step(g2, 0.0)
        self.v.answer(0, None)                          # check failed -> strict for this episode
        for t in (1.0, 8.0, 15.0):
            self.step(g2, t)
        self.assertEqual(len(self.v.calls), 1)


class DisabledTasks(unittest.TestCase):
    def test_enabled_false_keeps_the_numbers_but_not_the_task(self):
        v = AV.AlarmVerifier({"enabled": False, "tasks": {
            "robbery": {"enabled": False, "stage1_threshold": 0.6, "stage1_consecutive": 2, "cut": -6.0},
            "robbery_from_fight": {"stage1_threshold": 0.25, "stage1_consecutive": 1, "cut": -5.5}}}, ".")
        self.assertEqual(sorted(v.tasks), ["robbery_from_fight"])
        self.assertIsNone(v.stage1("robbery"))        # main.py then runs the robbery model only as fallback
        self.assertEqual(v.stage1("robbery_from_fight"), (0.25, 1))


class TwoQuestionRule(unittest.TestCase):
    """robbery_from_fight: yes/no >= cut AND robbery-vs-fight >= mc_cut;
    the second question only after the first passed."""
    TASKS = {"robbery_from_fight": {"stage1_threshold": 0.25, "stage1_consecutive": 1,
                                    "cut": -5.7, "mc_cut": 1.3}}

    def verdicts(self, yes, mc):
        v = AV.AlarmVerifier({"enabled": False, "tasks": self.TASKS, "reason": False}, ".")
        v.active, v.active_backend, v.status = True, "gpu", "ready"
        asked = []
        v.score = lambda task, clip: yes
        v.score_mc = lambda clip: asked.append(1) or mc
        got = []
        v.history.push(np.zeros((72, 128, 3), np.uint8), 0.0)
        v.submit("robbery_from_fight", lambda p, info, ctx: got.append((p, info)), end_ts=0.0)
        v._jobs.put(None)            # stop marker for this test
        orig_get = v._jobs.get

        def get():
            item = orig_get()
            if item is None:
                raise SystemExit
            return item
        v._jobs.get = get
        v._load = lambda: None
        try:
            v._worker()
        except SystemExit:
            pass
        return got[0], len(asked)

    def test_needs_both(self):
        (passed, info), asked = self.verdicts(-1.0, 0.5)    # criminal, but more fight than robbery
        self.assertFalse(passed)
        self.assertEqual(asked, 1)
        self.assertEqual(info["mc_score"], 0.5)
        (passed, _), _ = self.verdicts(-1.0, 2.0)
        self.assertTrue(passed)

    def test_second_question_only_after_the_first(self):
        (passed, info), asked = self.verdicts(-9.0, 9.0)
        self.assertFalse(passed)
        self.assertEqual(asked, 0)
        self.assertNotIn("mc_score", info)

    def test_can_run_needs_every_cut_for_the_backend(self):
        v = AV.AlarmVerifier({"enabled": False, "tasks": self.TASKS}, ".")
        self.assertFalse(v.can_run("robbery_from_fight"))           # not loaded
        v.active = True
        v.active_backend = "gpu"
        self.assertTrue(v.can_run("robbery_from_fight"))
        v.active_backend = "cpu"                                    # no cut_cpu / mc_cut_cpu
        self.assertFalse(v.can_run("robbery_from_fight"))
        v.tasks["robbery_from_fight"].update(cut_cpu=-6.0)
        self.assertFalse(v.can_run("robbery_from_fight"))           # mc_cut_cpu still missing
        v.tasks["robbery_from_fight"].update(mc_cut_cpu=1.0)
        self.assertTrue(v.can_run("robbery_from_fight"))


class LlamaScore(unittest.TestCase):
    def score(self, top):
        be = AV.LlamaBackend({}, ".")
        be._post = lambda *a: {"logprobs": {"content": [{"top_logprobs": top}]}}
        return be.score("q", None)

    def test_yes_minus_no_over_all_spellings(self):
        top = [{"token": "No", "logprob": np.log(0.6)}, {"token": "Yes", "logprob": np.log(0.2)},
               {"token": " Yes", "logprob": np.log(0.1)}, {"token": "Maybe", "logprob": np.log(0.1)}]
        self.assertAlmostEqual(self.score(top), np.log(0.3) - np.log(0.6), places=6)

    def test_mc_is_robbery_minus_fight(self):
        be = AV.LlamaBackend({}, ".")
        be._post = lambda *a: {"logprobs": {"content": [{"top_logprobs": [
            {"token": "A", "logprob": np.log(0.5)}, {"token": "B", "logprob": np.log(0.3)},
            {"token": " B", "logprob": np.log(0.1)}, {"token": "C", "logprob": np.log(0.1)}]}]}}
        self.assertAlmostEqual(be.score_mc(None), np.log(0.4) - np.log(0.5), places=6)

    def test_absent_side_is_bounded_by_the_top_list(self):
        top = [{"token": "No", "logprob": -0.01}, {"token": "x", "logprob": -9.0}]
        self.assertAlmostEqual(self.score(top), (-10.0) - (-0.01), places=6)

    def test_scores_never_reuse_the_prompt_cache(self):
        """The cuts were fitted on from-scratch scores; reusing the yes/no
        request's cached frames moved the multiple-choice score by up to 0.65."""
        import json
        from unittest import mock
        sent = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "x"}}]}).encode()

        def fake_urlopen(req, timeout=None):
            sent.append(json.loads(req.data))
            return Resp()

        be = AV.LlamaBackend({}, ".")
        be.url = "http://127.0.0.1:1/v1/chat/completions"
        clip = np.zeros((2, 32, 32, 3), np.uint8)
        with mock.patch.object(AV.urllib.request, "urlopen", fake_urlopen):
            be._post("q", clip, 1, True)
            be._post("q", clip, 40, False)
        self.assertIs(sent[0]["cache_prompt"], False)   # scored
        self.assertIs(sent[1]["cache_prompt"], True)    # description only


class WeaponCrop(unittest.TestCase):
    """The weapon check sees a square around the person, cut from the full-
    resolution frame (verify_weapon_shipped.py: 19/19 alerts, max diff 0.000)."""

    def test_box_is_square_padded_and_inside_the_frame(self):
        self.assertEqual(AV.weapon_crop_box([100, 100, 140, 200], 1280, 720), (20, 50, 220, 250))
        x1, y1, x2, y2 = AV.weapon_crop_box([1250, 650, 1280, 720], 1280, 720)   # clamped at the corner
        self.assertEqual((x2 - x1, y2 - y1), (140, 140))
        self.assertEqual((x2, y2), (1280, 720))

    def test_clip_is_cut_from_full_resolution_frames(self):
        h = AV.FrameHistory(seconds=4.0, side=None)
        for i in range(12):
            fr = np.zeros((720, 1280, 3), np.uint8)
            fr[50:250, 20:220] = 255                        # only the crop region is white
            h.push(fr, ts=i * 0.25)
        clip = h.crop_clip([100, 100, 140, 200], end_ts=2.75, n=8, span=2.0, side=448)
        self.assertEqual(clip.shape, (8, 448, 448, 3))
        self.assertGreater(clip.min(), 200)

    def test_submit_takes_a_ready_clip(self):
        v = AV.AlarmVerifier({"enabled": False, "tasks": {"weapon": {"cut": -5.8}}}, ".")
        v.active = True
        given = np.zeros((8, 64, 64, 3), np.uint8)
        self.assertTrue(v.submit("weapon", lambda *a: None, end_ts=1.0, context="x", clip=given))
        self.assertIs(v._jobs.get_nowait()[1], given)
        self.assertIsNotNone(v.full_history)               # kept only when the weapon task is on


class FrameHistoryReset(unittest.TestCase):
    def test_clear_after_a_new_stream(self):
        """main.py clears it on a reconnect or camera switch, so no check
        mixes frames from the old stream with the new one."""
        h = AV.FrameHistory(seconds=4.0, side=64)
        for i in range(10):
            h.push(np.full((48, 64, 3), 200, np.uint8), ts=i * 0.25)
        h.clear()
        for i in range(10):
            h.push(np.zeros((48, 64, 3), np.uint8), ts=10 + i * 0.25)
        clip = h.clip(12.25, n=8, span=2.0)
        self.assertEqual(int(clip.max()), 0)


if __name__ == "__main__":
    unittest.main()
