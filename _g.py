import sys, warnings, os, numpy as np, cv2
warnings.filterwarnings("ignore")
os.environ["ORT_LOGGING_LEVEL"] = "3"
sys.path.insert(0, r"C:\Users\July_RuoMoli\Documents\deepseek-harness\default-workspace\amane-work\face")
import onnxruntime as ort
from scrfd import ScrfdDetector
from PIL import Image
from pathlib import Path

base = Path(r"C:\Users\July_RuoMoli\Documents\deepseek-harness\default-workspace\face-test")
det = ScrfdDetector(str(base/"buffalo_l"/"det_10g.onnx"))
ga = ort.InferenceSession(str(base/"buffalo_l"/"genderage.onnx"), providers=["CPUExecutionProvider"])
SIZE = 96

def gender_score(rgb, bbox):
    x1,y1,x2,y2 = bbox
    w,h = x2-x1, y2-y1
    if w<=0 or h<=0: return None
    c = ((x1+x2)/2, (y1+y2)/2)
    s = SIZE / (max(w,h)*1.5)
    M = np.array([[s,0,-c[0]*s+SIZE/2],[0,s,-c[1]*s+SIZE/2]], dtype=np.float32)
    al = cv2.warpAffine(rgb, M, (SIZE,SIZE), borderValue=0.0)
    blob = cv2.dnn.blobFromImage(al, 1/128.0, (SIZE,SIZE), (127.5,127.5,127.5), swapRB=True)
    p = ga.run(None, {"data": blob})[0][0]
    # 返回女性置信度 (softmax 前两位)
    f, m = float(p[0]), float(p[1])
    return f / (f + m) if (f+m) != 0 else 0.5

lines = []
margins = []
for p in sorted((base/"carib").glob("*.jpg")):
    img = Image.open(p).convert("RGB")
    rgb = np.asarray(img)
    for f in det.detect(img, conf=0.45, max_side=640):
        gs = gender_score(rgb, f["bbox"])
        if gs is not None:
            margins.append(gs)

margins.sort()
lines.append(f"样本: {len(margins)} 张脸")
lines.append(f"女性置信度分布: min={margins[0]:.3f} p10={margins[len(margins)//10]:.3f} 中位={margins[len(margins)//2]:.3f} p90={margins[len(margins)*9//10]:.3f} max={margins[-1]:.3f}")
for th in [0.5, 0.6, 0.7, 0.8, 0.9]:
    n = sum(1 for m in margins if m >= th)
    lines.append(f"  阈值 {th}: {n}/{len(margins)} 判为女性")

out = base/"gender_result.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
