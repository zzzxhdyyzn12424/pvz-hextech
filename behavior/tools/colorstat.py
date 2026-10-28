import sys
from PIL import Image

p = sys.argv[1]
im = Image.open(p).convert("RGB")
print("size", im.size)
cols = im.getcolors(2000000)
cols.sort(reverse=True)
print("unique colors:", len(cols))
for c, rgb in cols[:8]:
    print("  ", c, rgb)
