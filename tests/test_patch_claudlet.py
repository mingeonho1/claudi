"""patch-claudlet.py 테스트: pristine pet.py 에 적용→검증→멱등→revert 원복.

pristine pet.py 확보 순서:
  1) 환경변수 CLAUDI_PRISTINE_PET_PY 가 가리키는 파일.
  2) `python3 -m pip download --no-deps claudlet==2.6.0` 로 휠을 받아 pet.py 추출.
둘 다 안 되면 테스트를 skip 한다(네트워크 없는 CI 대비). 업스트림 pet.py 는 리포에
커밋하지 않는다.
"""

import filecmp
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ENF_BIN

PATCH = os.path.join(ENF_BIN, "patch-claudlet.py")
MARKS = ["enforce-click-patch", "enforce-noreply-patch", "enforce-notice-patch",
         "enforce-noactivity-patch", "enforce-alerts-tab", "enforce-alerts-wire",
         "enforce-alerts-refresh", "enforce-alerts-poll", "enforce-alerts-methods",
         "enforce-alerts-slack"]


def _obtain_pristine(tmpdir):
    env = os.environ.get("CLAUDI_PRISTINE_PET_PY")
    if env and os.path.isfile(env):
        return env
    dl = os.path.join(tmpdir, "dl")
    os.makedirs(dl, exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps",
                        "claudlet==2.6.0", "-d", dl],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=300, check=True)
    except Exception:
        return None
    wheels = glob.glob(os.path.join(dl, "claudlet-*.whl"))
    if not wheels:
        return None
    ex = os.path.join(tmpdir, "ex")
    os.makedirs(ex, exist_ok=True)
    try:
        with zipfile.ZipFile(wheels[0]) as z:
            z.extractall(ex)
    except Exception:
        return None
    hits = glob.glob(os.path.join(ex, "**", "claudlet", "pet.py"), recursive=True)
    return hits[0] if hits else None


def _run(args):
    return subprocess.run([sys.executable, PATCH] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)


class PatchClaudletTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="claudi-patch-")
        cls.pristine = _obtain_pristine(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if not self.pristine:
            self.skipTest("pristine claudlet pet.py unavailable (set "
                          "CLAUDI_PRISTINE_PET_PY or allow pip download)")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work, exist_ok=True)
        self.pet = os.path.join(self.work, "pet.py")
        shutil.copy2(self.pristine, self.pet)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_full_lifecycle(self):
        # 1) 적용 전 --check 는 1(미적용).
        r = _run(["--pet-py", self.pet, "--check"])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

        # 2) 적용.
        r = _run(["--pet-py", self.pet])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # 3) py_compile 통과.
        c = subprocess.run([sys.executable, "-m", "py_compile", self.pet],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(c.returncode, 0, c.stderr)

        # 4) 모든 마커 존재.
        with open(self.pet, encoding="utf-8") as f:
            content = f.read()
        for m in MARKS:
            self.assertIn("[%s]" % m, content, "missing marker %s" % m)

        # 5) --check 는 0(모두 적용).
        r = _run(["--pet-py", self.pet, "--check"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # 6) 재적용은 멱등(아무것도 안 함).
        r = _run(["--pet-py", self.pet])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to do", r.stdout)

        # 7) revert 후 원본과 바이트 동일.
        r = _run(["--pet-py", self.pet, "--revert"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(filecmp.cmp(self.pristine, self.pet, shallow=False),
                        "revert 결과가 원본과 바이트 동일하지 않음")

        # 8) revert 후 --check 는 다시 1.
        r = _run(["--pet-py", self.pet, "--check"])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_patched_copy_has_no_personal_paths(self):
        _run(["--pet-py", self.pet])
        with open(self.pet, encoding="utf-8") as f:
            content = f.read()
        needle = "/" + "Users" + "/"          # 리터럴을 분해해 소스에 개인 경로가 남지 않게
        self.assertNotIn(needle, content)


if __name__ == "__main__":
    unittest.main()
