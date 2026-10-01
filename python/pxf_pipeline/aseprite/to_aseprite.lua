-- pxf import dir -> one .aseprite file a person can paint in.
--
--   Aseprite.exe -b --script-param dir=<pxf dir> --script-param out=<x.aseprite>
--                [--script-param layers=<dir1>;<dir2>] [--script-param aligned=0]
--                --script to_aseprite.lua
--
-- * one Aseprite frame per sheet frame, layer "art" holds the frame itself
-- * aligned (default when manifest.json has anchors from the .ani): each cel
--   sits at its first anchor, so the frames line up and play like the game
--   plays them; onion skin then shows real motion instead of jitter
-- * every "art" cel carries {index,file,x,y,w,h} as user data; from_aseprite.lua
--   crops by it, so moving cels around is safe and growing past the frame
--   box is reported instead of silently lost
-- * palette: the sheet's own colours. <= 255 of them -> Indexed mode with
--   index 0 transparent, i.e. the palette is truly locked. More (card art,
--   portraits) -> RGB mode with the 256 most used colours as the swatch set
-- * extra `layers` dirs (same frame_NNN.png names, e.g. a mask, a lifted
--   overlay, the stock frame, or `pxf downscale --confidence` output) become
--   layers above "art", hidden, locked, and tagged "reference" in their user
--   data. from_aseprite.lua never flattens a reference layer into the sprite,
--   even when it is left visible - turning the noise overlay on to look at it
--   must not paint it into the game. Their colours go into the palette after
--   the art's, so indexed mode does not snap them onto the nearest art colour
--   (it did: all 3184 noise marks came out as ce2839, the art's own lip and
--   tassel red, so they looked like part of the drawing). Alpha is made binary,
--   which indexed mode would have done anyway, only less predictably.

local P = app.params
local dir, out = P.dir, P.out
if not dir or not out then error("need --script-param dir=... and out=...") end
local sep = package.config:sub(1, 1)
local function join(a, b) return a .. sep .. b end

local function readall(p)
  local f = assert(io.open(p, "rb"), "cannot open " .. p)
  local s = f:read("a"); f:close(); return s
end

local m = json.decode(readall(join(dir, "manifest.json")))
local frames = {}
for _, fe in ipairs(m.frames) do
  if fe.file then frames[#frames + 1] = fe end
end
if #frames == 0 then error("no frames with images in " .. dir) end

local aligned = P.aligned ~= "0"
local any_anchor = false
for _, fe in ipairs(frames) do if fe.anchors and #fe.anchors > 0 then any_anchor = true end end
aligned = aligned and any_anchor

-- placement of each frame on the canvas
local ox, oy, ex, ey = math.huge, math.huge, -math.huge, -math.huge
for _, fe in ipairs(frames) do
  local ax, ay = 0, 0
  if aligned and fe.anchors and #fe.anchors > 0 then
    ax, ay = math.floor(fe.anchors[1][1]), math.floor(fe.anchors[1][2])
  end
  fe._x, fe._y = ax, ay
  ox, oy = math.min(ox, ax), math.min(oy, ay)
  ex, ey = math.max(ex, ax + fe.width), math.max(ey, ay + fe.height)
end
local W, H = ex - ox, ey - oy

local spr = Sprite(W, H, ColorMode.RGB)
spr.filename = out
local art = spr.layers[1]
art.name = "art"

-- colour census for the palette
local count, order = {}, {}
for i, fe in ipairs(frames) do
  if i > 1 then spr:newEmptyFrame() end
  local img = Image{ fromFile = join(dir, fe.file) }
  for px in img:pixels() do
    local v = px()
    if app.pixelColor.rgbaA(v) > 0 then
      local k = v | 0xff000000
      if not count[k] then count[k] = 0; order[#order + 1] = k end
      count[k] = count[k] + 1
    end
  end
  local cel = spr:newCel(art, i, img, Point(fe._x - ox, fe._y - oy))
  cel.data = json.encode{ index = fe.index, file = fe.file, x = fe._x - ox, y = fe._y - oy,
                          w = fe.width, h = fe.height }
end

-- reference layers (added while still RGB; the pixel format change below
-- maps them onto the palette with everything else)
local nref = 0
local refcount, reforder = {}, {}
if P.layers and P.layers ~= "" then
  for ldir in string.gmatch(P.layers, "[^;]+") do
    local name = ldir:match("([^/\\]+)[/\\]*$") or ldir
    local layer = spr:newLayer()
    layer.name = name
    layer.data = "reference"
    for i, fe in ipairs(frames) do
      local p = join(ldir, fe.file)
      local f = io.open(p, "rb")
      if f then
        f:close()
        local img = Image{ fromFile = p }
        for px in img:pixels() do
          local v = px()
          if app.pixelColor.rgbaA(v) > 0 then
            local k = v | 0xff000000
            px(k)
            if not count[k] and not refcount[k] then refcount[k] = 0; reforder[#reforder + 1] = k end
            if refcount[k] then refcount[k] = refcount[k] + 1 end
          else
            px(0)
          end
        end
        spr:newCel(layer, i, img, Point(fe._x - ox, fe._y - oy))
      end
    end
    layer.isVisible = false
    layer.isEditable = false
    nref = nref + 1
  end
end

table.sort(order, function(a, b) return count[a] > count[b] end)
local nart = #order
-- reference-only colours after the art's own, so the art keeps the low indices
-- and its swatches come first; still sorted by use among themselves
table.sort(reforder, function(a, b) return refcount[a] > refcount[b] end)
for _, k in ipairs(reforder) do order[#order + 1] = k end
local ncol = #order
local indexed = ncol <= 255
local take = indexed and ncol or math.min(ncol, 256)
local pal = Palette(take + (indexed and 1 or 0))
local base = 0
if indexed then pal:setColor(0, Color{ r = 0, g = 0, b = 0, a = 0 }); base = 1 end
for i = 1, take do
  local v = order[i]
  pal:setColor(base + i - 1, Color{ r = app.pixelColor.rgbaR(v), g = app.pixelColor.rgbaG(v),
                                    b = app.pixelColor.rgbaB(v), a = 255 })
end
spr:setPalette(pal)
if indexed then
  app.command.ChangePixelFormat{ format = "indexed", dithering = "none" }
  spr.transparentColor = 0
end

spr:saveAs(out)
print(string.format("%s: %d frame(s), canvas %dx%d, %s, %d colour(s) (%d art + %d reference-only)%s, %d reference layer(s)",
  out, #frames, W, H, aligned and "aligned by .ani anchors" or "unaligned",
  ncol, nart, ncol - nart, indexed and " -> indexed, palette locked" or " -> RGB, 256 swatches", nref))
