import sys
import time

print("fixture main start:", " ".join(sys.argv[1:]))
for index in range(3):
    print(f"tick {index}")
    time.sleep(0.05)
print("fixture main done")
