# portfolio.tpl.html + imgs.json -> ../portfolio.html (스크린샷을 base64로 삽입)
import json, os
here = os.path.dirname(os.path.abspath(__file__))
imgs = json.load(open(os.path.join(here, 'imgs.json')))
t = open(os.path.join(here, 'portfolio.tpl.html'), encoding='utf-8').read()
t = t.replace('{{IMGS_JSON}}', json.dumps(imgs))
for k, v in imgs.items():
    t = t.replace('{{IMG_' + k + '}}', v)
assert '{{' not in t, 'unreplaced placeholder'
open(os.path.join(here, '..', 'portfolio.html'), 'w', encoding='utf-8').write(t)
print('built portfolio.html')
