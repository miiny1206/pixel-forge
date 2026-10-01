-- .aseprite written by to_aseprite.lua -> frame PNGs back into the pxf dir.
--
--   Aseprite.exe -b --script-param in=<x.aseprite> --script-param dir=<pxf dir>
--                [--script-param layers=art;overlay] --script from_aseprite.lua
--
-- Every visible layer (or the ones named in `layers`) is flattened, bottom to
-- top, into the frame's own box. Layers tagged "reference" by to_aseprite.lua
-- (stock frame, masks, the noise overlay) are skipped even when visible; name
-- one in `layers` to take it on purpose — the {x,y,w,h} the "art" cel carries as user
-- data. Then `pxf export` rebuilds the .spr. Reported, not fixed:
--   outside   painted pixels that fall outside the frame box (they are lost:
--             the box size is fixed by the sheet; grow it with pxf, not here)
--   partial   pixels with 0 < alpha < 255 (a .spr has no alpha; pxf export
--             turns them fully opaque or into the colour key)

local P = app.params
local src, dir = P["in"], P.dir
if not src or not dir then error("need --script-param in=... and dir=...") end
local sep = package.config:sub(1, 1)

local spr = app.open(src)
if not spr then error("cannot open " .. src) end
if spr.colorMode ~= ColorMode.RGB then
  app.command.ChangePixelFormat{ format = "rgb" }   -- in memory only, never saved
end

local want
if P.layers and P.layers ~= "" then
  want = {}
  for n in string.gmatch(P.layers, "[^;]+") do want[n] = true end
end

local art
for _, l in ipairs(spr.layers) do if l.name == "art" then art = l end end
if not art then error(src .. " has no layer named \"art\" (not written by to_aseprite.lua?)") end

local layers = {}
for _, l in ipairs(spr.layers) do          -- bottom to top
  local ref = l.data == "reference"
  if not l.isGroup and ((want and want[l.name]) or (not want and l.isVisible and not ref)) then
    layers[#layers + 1] = l
  end
end

local written, outside, partial = 0, 0, 0
for _, fr in ipairs(spr.frames) do
  local c = art:cel(fr)
  if c and c.data and c.data ~= "" then
    local d = json.decode(c.data)
    local x, y, w, h = math.floor(d.x), math.floor(d.y), math.floor(d.w), math.floor(d.h)
    local img = Image(w, h, ColorMode.RGB)
    for _, l in ipairs(layers) do
      local cel = l:cel(fr)
      if cel then
        img:drawImage(cel.image, Point(cel.position.x - x, cel.position.y - y),
                      math.floor(cel.opacity * l.opacity / 255), BlendMode.NORMAL)
        for px in cel.image:pixels() do
          if app.pixelColor.rgbaA(px()) > 0 then
            local gx, gy = cel.position.x + px.x, cel.position.y + px.y
            if gx < x or gy < y or gx >= x + w or gy >= y + h then outside = outside + 1 end
          end
        end
      end
    end
    for px in img:pixels() do
      local a = app.pixelColor.rgbaA(px())
      if a > 0 and a < 255 then partial = partial + 1 end
    end
    img:saveAs(dir .. sep .. d.file)
    written = written + 1
  end
end
print(string.format("%s: %d frame(s) -> %s, %d px outside their frame box, %d partially transparent px",
  src, written, dir, outside, partial))
