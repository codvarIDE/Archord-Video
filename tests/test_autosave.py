import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from archord_video.core import autosave as au
from archord_video.core.fileio import atomic_write_text
from archord_video.core.project import Clip, MediaItem, Project


def dead_pid():
    """A process id that certainly belonged to a process that has finished."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def make_project():
    p = Project()
    p.add_media(MediaItem("m1", "/tmp/a.mp4", "video", 10.0, 1920, 1080, 30, True))
    p.add_clip("m1", 1, 2.0)
    return p


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "Autosave")
        self.addCleanup(self._tmp.cleanup)

    def crashed_session(self, project=None, project_path=None):
        """Leave behind what a session that died without a clean exit leaves behind."""
        a = au.Autosaver(self.dir)
        a.start()
        self.assertTrue(a.write((project or make_project()).to_dict(), project_path))
        with open(a.lock, "w") as f:
            json.dump({"pid": dead_pid()}, f)
        return a


class AtomicWriteTests(TmpDirCase):
    def test_writes_and_replaces(self):
        path = os.path.join(self._tmp.name, "a.txt")
        atomic_write_text(path, "one")
        atomic_write_text(path, "two")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "two")
        self.assertEqual(os.listdir(self._tmp.name), ["a.txt"])      # no temp files left behind

    def test_failure_keeps_old_file_and_cleans_up(self):
        path = os.path.join(self._tmp.name, "a.txt")
        atomic_write_text(path, "good")
        with mock.patch("archord_video.core.fileio.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_text(path, "bad")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "good")
        self.assertEqual(os.listdir(self._tmp.name), ["a.txt"])

    def test_unicode(self):
        path = os.path.join(self._tmp.name, "u.txt")
        atomic_write_text(path, "Café – 日本語")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "Café – 日本語")

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_keeps_permissions_of_existing_file(self):
        path = os.path.join(self._tmp.name, "a.txt")
        atomic_write_text(path, "x")
        os.chmod(path, 0o640)
        atomic_write_text(path, "y")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)


class ProjectSaveTests(TmpDirCase):
    def test_save_load_roundtrip(self):
        path = os.path.join(self._tmp.name, "p.avproj")
        p = make_project()
        p.save(path)
        q = Project.load(path)
        self.assertEqual(q.to_dict(), p.to_dict())

    def test_failed_save_does_not_damage_existing_project(self):
        path = os.path.join(self._tmp.name, "p.avproj")
        p = make_project()
        p.save(path)
        before = read(path)
        with mock.patch("archord_video.core.fileio.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                p.save(path)
        self.assertEqual(read(path), before)
        self.assertEqual(Project.load(path).to_dict(), p.to_dict())


class AutosaverTests(TmpDirCase):
    def test_write_and_roundtrip_through_recovery(self):
        p = make_project()
        self.crashed_session(p, "/home/me/film.avproj")
        found = au.find_recoverable(self.dir, exclude_session="another-session")
        self.assertEqual(len(found), 1)
        e = found[0]
        self.assertEqual(e.project_path, "/home/me/film.avproj")
        self.assertEqual(e.name, "film.avproj")
        self.assertEqual(Project.from_dict(e.project).to_dict(), p.to_dict())

    def test_never_saved_project_is_called_untitled(self):
        self.crashed_session(project_path=None)
        self.assertEqual(au.find_recoverable(self.dir)[0].name, "Untitled")

    def test_clean_exit_leaves_nothing(self):
        a = au.Autosaver(self.dir)
        a.start()
        a.write(make_project().to_dict())
        a.close()
        self.assertEqual(os.listdir(self.dir), [])
        self.assertEqual(au.find_recoverable(self.dir), [])

    def test_clear_after_save_removes_snapshot_but_keeps_session_alive(self):
        a = au.Autosaver(self.dir)
        a.start()
        a.write(make_project().to_dict())
        a.clear()
        self.assertFalse(os.path.exists(a.path))
        self.assertTrue(os.path.exists(a.lock))

    def test_own_session_is_never_offered(self):
        a = au.Autosaver(self.dir)
        a.start()
        a.write(make_project().to_dict())
        os.remove(a.lock)                                              # even with no lock at all
        self.assertEqual(au.find_recoverable(self.dir, exclude_session=a.session), [])

    def test_other_running_window_is_not_offered(self):
        other = au.Autosaver(self.dir)
        other.start()                                                  # lock holds our (alive) pid, fresh
        other.write(make_project().to_dict())
        self.assertEqual(au.find_recoverable(self.dir, exclude_session="me"), [])

    def test_dead_process_is_offered(self):
        self.crashed_session()
        self.assertEqual(len(au.find_recoverable(self.dir)), 1)

    def test_stale_heartbeat_is_offered_even_if_pid_is_reused(self):
        a = au.Autosaver(self.dir)
        a.start()                                                      # pid is alive (us), as after a pid reuse
        a.write(make_project().to_dict())
        old = time.time() - au.HEARTBEAT_TTL - 5
        os.utime(a.lock, (old, old))
        self.assertEqual(len(au.find_recoverable(self.dir)), 1)

    def test_heartbeat_keeps_session_alive(self):
        a = au.Autosaver(self.dir)
        a.start()
        a.write(make_project().to_dict())
        old = time.time() - au.HEARTBEAT_TTL - 5
        os.utime(a.lock, (old, old))
        a.heartbeat()
        self.assertEqual(au.find_recoverable(self.dir), [])

    def test_heartbeat_recreates_missing_lock(self):
        a = au.Autosaver(self.dir)
        a.start()
        os.remove(a.lock)
        a.heartbeat()
        self.assertTrue(os.path.exists(a.lock))

    def test_missing_lock_is_offered(self):
        a = self.crashed_session()
        os.remove(a.lock)
        self.assertEqual(len(au.find_recoverable(self.dir)), 1)

    def test_corrupt_snapshot_is_set_aside_not_offered(self):
        os.makedirs(self.dir)
        bad = os.path.join(self.dir, "x" + au.AUTOSAVE_EXT)
        with open(bad, "w") as f:
            f.write('{"format": 1, "project": {"width": 19')           # truncated
        self.assertEqual(au.find_recoverable(self.dir), [])
        self.assertFalse(os.path.exists(bad))
        self.assertTrue(os.path.exists(bad + au.CORRUPT_EXT))
        self.assertEqual(au.find_recoverable(self.dir), [])            # and stays quiet on the next start

    def test_snapshot_from_other_format_is_left_alone(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "x" + au.AUTOSAVE_EXT)
        with open(path, "w") as f:
            json.dump({"format": 99, "project": {}}, f)
        self.assertEqual(au.find_recoverable(self.dir), [])
        self.assertTrue(os.path.exists(path))

    def test_newest_first(self):
        a = self.crashed_session()
        b = self.crashed_session()
        for sess, t in ((a, 1000.0), (b, 2000.0)):
            with open(sess.path, encoding="utf-8") as f:
                d = json.load(f)
            d["saved_at"] = t
            with open(sess.path, "w", encoding="utf-8") as f:
                json.dump(d, f)
        self.assertEqual([e.session for e in au.find_recoverable(self.dir)], [b.session, a.session])

    def test_orphan_lock_and_old_temp_files_are_tidied(self):
        a = au.Autosaver(self.dir)
        a.start()
        with open(a.lock, "w") as f:
            json.dump({"pid": dead_pid()}, f)
        junk = os.path.join(self.dir, ".tmp-abc.part")
        with open(junk, "w") as f:
            f.write("x")
        old = time.time() - au.JUNK_AGE - 5
        os.utime(junk, (old, old))
        au.find_recoverable(self.dir)
        self.assertEqual(os.listdir(self.dir), [])

    def test_missing_directory_is_fine(self):
        self.assertEqual(au.find_recoverable(os.path.join(self.dir, "nope")), [])

    def test_discard_and_export(self):
        p = make_project()
        self.crashed_session(p)
        e = au.find_recoverable(self.dir)[0]
        out = os.path.join(self._tmp.name, "Recovered.avproj")
        au.export(e, out)
        self.assertEqual(Project.load(out).to_dict(), p.to_dict())
        au.discard(e)
        self.assertEqual(au.find_recoverable(self.dir), [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_quarantine_stops_offering_but_keeps_the_file(self):
        a = self.crashed_session()
        e = au.find_recoverable(self.dir)[0]
        au.quarantine(e)
        self.assertEqual(au.find_recoverable(self.dir), [])
        self.assertTrue(os.path.exists(a.path + au.CORRUPT_EXT))

    def test_write_failure_returns_false_instead_of_raising(self):
        a = au.Autosaver(self.dir)
        a.start()
        with mock.patch("archord_video.core.autosave.atomic_write_text", side_effect=OSError("disk full")):
            self.assertFalse(a.write(make_project().to_dict()))
        self.assertIsNotNone(a.last_error)

    def test_unwritable_directory_does_not_raise(self):
        blocker = os.path.join(self._tmp.name, "file")
        open(blocker, "w").close()
        a = au.Autosaver(os.path.join(blocker, "sub"))                 # a "folder" below a regular file
        self.assertFalse(a.start())
        self.assertFalse(a.write(make_project().to_dict()))


class SyncAndRescueTests(TmpDirCase):
    def test_sync_writes_only_when_something_changed(self):
        a = au.Autosaver(self.dir)
        a.start()
        snap = make_project().snapshot()
        with mock.patch.object(a, "write", wraps=a.write) as w:
            self.assertTrue(a.sync(snap, None))
            self.assertTrue(a.sync(snap, None))                        # unchanged: no second write
            self.assertEqual(w.call_count, 1)
            self.assertTrue(a.sync(snap, "/x/film.avproj"))            # same project saved elsewhere: rewrite
            self.assertEqual(w.call_count, 2)
            p = make_project()
            p.add_clip("m1", 1, 20.0)
            self.assertTrue(a.sync(p.snapshot(), "/x/film.avproj"))    # edited: rewrite
            self.assertEqual(w.call_count, 3)

    def test_sync_after_clear_writes_again(self):
        a = au.Autosaver(self.dir)
        a.start()
        snap = make_project().snapshot()
        a.sync(snap)
        a.clear()
        self.assertFalse(os.path.exists(a.path))
        self.assertTrue(a.sync(snap))
        self.assertTrue(os.path.exists(a.path))

    def test_sync_failure_is_retried_next_time(self):
        a = au.Autosaver(self.dir)
        a.start()
        snap = make_project().snapshot()
        with mock.patch("archord_video.core.autosave.atomic_write_text", side_effect=OSError("disk full")):
            self.assertFalse(a.sync(snap))
        self.assertTrue(a.sync(snap))
        self.assertEqual(Project.from_dict(json.loads(read(a.path))["project"]).to_dict(),
                         Project.from_snapshot(snap).to_dict())

    def test_synced_snapshot_survives_a_crash_and_recovers(self):
        a = au.Autosaver(self.dir)
        a.start()
        p = make_project()
        a.sync(p.snapshot(), "/x/film.avproj")
        with open(a.lock, "w") as f:                                   # the process dies
            json.dump({"pid": dead_pid()}, f)
        e = au.find_recoverable(self.dir)[0]
        self.assertEqual(Project.from_dict(e.project).to_dict(), p.to_dict())

    def test_rescue_saves_files_and_never_overwrites(self):
        for _ in range(3):
            self.crashed_session(project_path="/x/film.avproj")
        entries = au.find_recoverable(self.dir)
        for e in entries:
            e.saved_at = 1_700_000_000.0                               # identical timestamps: names would clash
        folder = os.path.join(self._tmp.name, "Recovered projects")
        self.assertEqual(au.rescue(entries, folder), 3)
        names = sorted(os.listdir(folder))
        self.assertEqual(len(names), 3)
        self.assertTrue(all(n.startswith("film (recovered ") and n.endswith(".avproj") for n in names))
        for n in names:
            Project.load(os.path.join(folder, n))                      # each is a valid project file
        self.assertEqual(au.find_recoverable(self.dir), [])            # and nothing is left to offer again

    def test_rescue_failure_keeps_snapshots(self):
        self.crashed_session()
        entries = au.find_recoverable(self.dir)
        blocker = os.path.join(self._tmp.name, "file")
        open(blocker, "w").close()
        self.assertEqual(au.rescue(entries, os.path.join(blocker, "sub")), 0)
        self.assertEqual(len(au.find_recoverable(self.dir)), 1)


class RealCrashTests(TmpDirCase):
    CHILD = (
        "import os, sys\n"
        "from archord_video.core import autosave as au\n"
        "from archord_video.core.project import MediaItem, Project\n"
        "a = au.Autosaver(sys.argv[1], session='crashy')\n"
        "a.start()\n"
        "p = Project()\n"
        "p.add_media(MediaItem('m1', '/tmp/a.mp4', 'video', 10.0, 1920, 1080, 30, True))\n"
        "p.add_clip('m1', 1, 2.0)\n"
        "a.sync(p.snapshot(), None)\n"
        "print('ready', flush=True)\n"
        "os._exit(1)          # dies like a crash: no cleanup code runs\n"
    )

    def test_work_survives_a_process_that_dies_without_cleanup(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", self.CHILD, self.dir], cwd=root, capture_output=True, text=True)
        self.assertIn("ready", r.stdout, r.stderr)
        found = au.find_recoverable(self.dir, exclude_session="the-next-session")
        self.assertEqual([e.session for e in found], ["crashy"])
        self.assertEqual(len(Project.from_dict(found[0].project).clips), 1)


class PidAliveTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(au.pid_alive(os.getpid()))

    def test_finished_process_is_dead(self):
        self.assertFalse(au.pid_alive(dead_pid()))

    def test_garbage(self):
        for bad in (None, "x", 0, -5):
            self.assertFalse(au.pid_alive(bad))


if __name__ == "__main__":
    unittest.main()
