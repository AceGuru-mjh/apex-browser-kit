import shutil, subprocess, sys, tempfile
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent.parent  # repo root
res=[]
def run(repo): return subprocess.run([sys.executable,str(repo/"scripts/check_code_quality.py")],capture_output=True,text=True,cwd=repo)
def case(name,mut,expect):
    with tempfile.TemporaryDirectory() as t:
        repo=Path(t)/"r"; shutil.copytree(SRC,repo,ignore=shutil.ignore_patterns(".git","build",".gradle",".kotlin"))
        mut(repo); p=run(repo); o=p.stdout+p.stderr
        ok=p.returncode!=0 and expect in o; res.append((ok,name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       exit={p.returncode} expect={expect!r}")
            for l in o.splitlines()[:6]: print("       "+l)
def godfile(repo):
    f=repo/"browser-core/src/main/kotlin/com/apex/browser/core/JsLiteral.kt"
    f.write_text(f.read_text(encoding="utf-8")+"\n// x\n"*1300,encoding="utf-8")
case("catches a main-source God file (size budget)",godfile,"GATE 1")
def st(repo):
    f=repo/"browser-core/src/main/kotlin/com/apex/browser/core/JsLiteral.kt"
    f.write_text(f.read_text(encoding="utf-8")+"\nfun boom(){ try{}catch(e:Throwable){ e.printStackTrace() } }\n",encoding="utf-8")
case("catches printStackTrace() in main sources",st,"GATE 2")
def rf(repo):
    f=repo/"browser-core/src/main/kotlin/com/apex/browser/core/JsLiteral.kt"
    f.write_text(f.read_text(encoding="utf-8")+"\nfun d(x:Any)=x.javaClass.getMethod(\"foo\")\n",encoding="utf-8")
case("catches javaClass.getMethod reflective dispatch",rf,"GATE 3")
ok=sum(1 for r,_ in res if r); print(f"\n{ok}/{len(res)} code-quality negative-tests correct"); sys.exit(0 if ok==len(res) else 1)
