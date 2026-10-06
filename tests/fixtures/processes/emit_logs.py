import sys

for _ in range(1000):
    sys.stdout.write("x" * 1024)
    sys.stdout.flush()
