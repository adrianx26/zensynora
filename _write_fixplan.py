
import os

content = open(r'F:\\ANTI\\zensynora\\fixplan.md', 'r', encoding='utf-8').read()

content += open(r'F:\\ANTI\\zensynora\\fixplan.md', 'r', encoding='utf-8').read()

with open(r'F:\\ANTI\\zensynora\\fixplan.md', 'a', encoding='utf-8') as f:
    f.write('DONE')
