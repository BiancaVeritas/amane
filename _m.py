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

def raw(rgb, bbox):
    x1,y1,x2,y2 = bbox
    w,h = x2-x1, y2-y1
    c = ((x1+x2)/2, (y1+y2)/2)
    s = SIZE / (max(w,h)*1.5)
    M = np.array([[s,0,-c[0]*s+SIZE/2],[0,s,-c[1]*s+SIZE/2]], dtype=np.float32)
    al = cv2.warpAffine(rgb, M, (SIZE,SIZE), borderValue=0.0)
    blob = cv2.dnn.blobFromImage(al, 1/128.0, (SIZE,SIZE), (127.5,127.5,127.5), swapRB=True)
    return ga.run(None, {"data": blob})[0][0]

lines = []
for p in sorted((base/"carib").glob("*.jpg")):
    img = Image.open(p).convert("RGB")
    rgb = np.asarray(img)
    for f in det.detect(img, conf=0.45, max_side=640):
        pred = raw(rgb, f["bbox"])
        is_male = int(np.argmax(pred[:2])) == 1
        if is_male:
            b = f["bbox"]
            lines.append(f"{p.name}  男  bbox=({b[0]:.0f},{b[1]:.0f})-({b[2]:.0f},{b[3]:.0f}) 面积={ (b[2]-b[0])*(b[3]-b[1]):.0f}  score={f['score']:.2f}")
out = base/"male_faces.txt"
out.write_text("\n".join(lines) if lines else "无男性", encoding="utf-8")
print("done")
