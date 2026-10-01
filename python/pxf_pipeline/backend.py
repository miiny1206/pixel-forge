"""One image edit against an OpenAI-compatible endpoint, in either of two request styles.

    python -m pxf_pipeline.backend chat   <in.png> <out.png> --prompt-file p.txt [--model M] [--ref r.png ...]
    python -m pxf_pipeline.backend images <in.png> <out.png> --prompt-file p.txt [--model M] [--ref r.png ...] [--mask m.png]

Configuration (environment or .env, never printed):

    PXF_API_BASE          base URL of the endpoint, e.g. https://api.example.com (no /v1)
    PXF_API_KEY           bearer token
    PXF_IMAGE_MODEL       model for the `images` style   (POST /v1/images/edits, multipart)
    PXF_CHAT_IMAGE_MODEL  model for the `chat` style     (POST /v1/chat/completions with
                          "modalities": ["image", "text"]; the image comes back inside the
                          assistant message)

Why both styles. They were measured to behave differently on the same frames:

  images  an image-edit model (a GPT Image model in our runs) took a whole sheet of 4-6 frames
          and kept the grid: 76-87% of its strong edges on a block boundary. It always answered
          1254x1254 whatever size was asked, a uniform rescale `pxf downscale` handles.
  chat    a Gemini image model behind a chat-completions gateway, ONE frame per request and
          no reference image: grid fit 95-97% at scale 1.000, 120-350 weak pixels. Given
          several frames or a reference it redrew the layout or copied the reference. It
          answers JPEG, which is re-encoded to PNG here (pxf reads PNG only).

Refusals. When the model declines (a safety finish reason, a moderation error) the exit
message says so and the raw answer, image bytes cut out, is kept next to the output as
*.error.json. The pipeline never resends a refused frame with other wording or another crop.
"""
import base64, io, json, os, re, sys, time, urllib.error, urllib.request, uuid

from . import env


def _endpoint(path):
    base = env.get('PXF_API_BASE', required=True).rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    return base + path


def _key():
    return env.get('PXF_API_KEY', required=True)


def _post(url, body, ctype):
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + _key(), 'Content-Type': ctype})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit('HTTP %d: %s' % (e.code, e.read()[:500].decode('utf-8', 'replace')))


def data_url(p):
    return 'data:image/png;base64,' + base64.b64encode(open(p, 'rb').read()).decode()


def find_image(d):
    """first base64 image anywhere in the answer, as bytes: where it sits depends on how the
    gateway translates the model's answer, so every place it has been seen is looked at"""
    s = json.dumps(d)
    m = re.search(r'data:image/[a-z]+;base64,([A-Za-z0-9+/=]+)', s)
    if m:
        return base64.b64decode(m.group(1))
    m = re.search(r'"(?:b64_json|data)": "([A-Za-z0-9+/=]{1000,})"', s)
    return base64.b64decode(m.group(1)) if m else None


def strip(d):
    return json.loads(re.sub(r'[A-Za-z0-9+/=]{1000,}', '<image bytes>', json.dumps(d)))


def chat(src, dst, prompt, model=None, refs=()):
    model = model or env.get('PXF_CHAT_IMAGE_MODEL', required=True)
    content = [{'type': 'text', 'text': prompt}] + \
        [{'type': 'image_url', 'image_url': {'url': data_url(p)}} for p in [src] + list(refs)]
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': content}],
                       'modalities': ['image', 'text']}).encode()
    t = time.time()
    d = json.loads(_post(_endpoint('/v1/chat/completions'), body, 'application/json'))
    meta = strip(d)
    meta['model'], meta['refs'], meta['seconds'] = model, list(refs), round(time.time() - t, 1)
    img = find_image(d)
    if not img:
        json.dump(meta, open(os.path.splitext(dst)[0] + '.error.json', 'w'), indent=1)
        # the reason first: a safety refusal ("prohibited_content") was once cut off the
        # end of this message and got retried as if the answer had merely been empty
        why = [c.get('finish_reason') for c in d.get('choices', [])]
        raise SystemExit('no image returned (finish_reason %s): %s' % (why, json.dumps(meta)[:400]))
    from PIL import Image
    im = Image.open(io.BytesIO(img))
    meta['format'] = im.format
    im.convert('RGB').save(dst, 'PNG')
    json.dump(meta, open(os.path.splitext(dst)[0] + '.meta.json', 'w'), indent=1)
    return len(img), meta['seconds']


def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [b'--' + b.encode(), ('Content-Disposition: form-data; name="%s"' % k).encode(), b'', v.encode()]
    for k, fn, data in files:
        out += [b'--' + b.encode(),
                ('Content-Disposition: form-data; name="%s"; filename="%s"' % (k, fn)).encode(),
                b'Content-Type: image/png', b'', data]
    out += [b'--' + b.encode() + b'--', b'']
    return b'\r\n'.join(out), 'multipart/form-data; boundary=' + b


def images(src, dst, prompt, model=None, refs=(), mask=None, size='1024x1024'):
    """--ref images go after the one being edited (multipart `image[]`), e.g. an approved
    sheet, so an outfit drawn once is the outfit every later sheet is told to copy"""
    model = model or env.get('PXF_IMAGE_MODEL', required=True)
    if refs:
        files = [('image[]', os.path.basename(p), open(p, 'rb').read()) for p in [src] + list(refs)]
    else:
        files = [('image', os.path.basename(src), open(src, 'rb').read())]
    if mask:
        files.append(('mask', 'mask.png', open(mask, 'rb').read()))
    body, ctype = _multipart({'model': model, 'prompt': prompt, 'n': '1', 'size': size}, files)
    t = time.time()
    raw = _post(_endpoint('/v1/images/edits'), body, ctype)
    d = json.loads(raw)
    if not d.get('data'):
        # some gateways answer 200 with an error body; keep it for the record
        open(os.path.splitext(dst)[0] + '.error.json', 'wb').write(raw)
        raise SystemExit('no image returned: %s' % raw[:400].decode('utf-8', 'replace'))
    item = d['data'][0]
    if item.get('b64_json'):
        img = base64.b64decode(item['b64_json'])
    elif item.get('url'):
        img = urllib.request.urlopen(item['url'], timeout=120).read()
    else:
        raise SystemExit('no image in response: %s' % list(item))
    open(dst, 'wb').write(img)
    meta = {k: v for k, v in d.items() if k != 'data'}
    meta.update(item_keys=sorted(item), revised_prompt=item.get('revised_prompt'), model=model,
                refs=list(refs), seconds=round(time.time() - t, 1))
    json.dump(meta, open(os.path.splitext(dst)[0] + '.meta.json', 'w'), indent=1)
    return len(img), meta['seconds']


def available(model=None):
    """True unless the endpoint answers 429 (all credentials rate limited). A tiny text-only
    chat call to the image model: costs a request but no image."""
    model = model or env.get('PXF_CHAT_IMAGE_MODEL') or env.get('PXF_IMAGE_MODEL')
    body = json.dumps({'model': model, 'max_tokens': 5,
                       'messages': [{'role': 'user', 'content': 'Reply with the single word: ok'}]}).encode()
    req = urllib.request.Request(_endpoint('/v1/chat/completions'), data=body, headers={
        'Authorization': 'Bearer ' + _key(), 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=120)
        return True
    except urllib.error.HTTPError as e:
        return e.code != 429
    except Exception:
        return False


def _opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3 or argv[0] not in ('chat', 'images') or '--prompt-file' not in argv:
        raise SystemExit(__doc__)
    style, src, dst = argv[:3]
    prompt = open(_opt(argv, '--prompt-file'), encoding='utf-8').read()
    refs = [argv[i + 1] for i, a in enumerate(argv) if a == '--ref']
    model = _opt(argv, '--model')
    if style == 'chat':
        n, s = chat(src, dst, prompt, model, refs)
    else:
        n, s = images(src, dst, prompt, model, refs, _opt(argv, '--mask'))
    print('%s: %d bytes in %.0fs' % (dst, n, s))


if __name__ == '__main__':
    main()
