import sys, warnings, os, time
warnings.filterwarnings("ignore")
os.environ["ORT_LOGGING_LEVEL"] = "3"
sys.path.insert(0, r"C:\Users\July_RuoMoli\Documents\deepseek-harness\default-workspace\amane-work\face")
from scrfd import ScrfdDetector
from PIL import Image
from pathlib import Path

base = Path(r"C:\Users\July_RuoMoli\Documents\deepseek-harness\default-workspace\face-test")
files = sorted((base/"carib").glob("*.jpg"))[:30]
det = ScrfdDetector(str(base/"buffalo_l"/"det_10g.onnx"))

lines = []
for ms in [640, 1024]:
    detected = total = small = 0
    t0 = time.perf_counter()
    for p in files:
        faces = det.detect(Image.open(p), conf=0.45, max_side=ms)
        if faces: detected += 1
        total += len(faces)
        small += sum(1 for f in faces if f[3] - f[1] < 40)
    ms_per = (time.perf_counter()-t0)*1000/len(files)
    lines.append(f"max_side={ms} 检出{detected}/30 总脸{total} 小脸{small} {ms_per:.1f}ms/张")

out = Path(r"C:\Users\July_RuoMoli\Documents\deepseek-harness\default-workspace\face-test\ms_result.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
