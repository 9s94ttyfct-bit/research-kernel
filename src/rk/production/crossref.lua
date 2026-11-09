-- Cross-references for the export (Plan 5c Task 6): resolves the citations ``@fig:<id>`` and ``@tbl:<id>`` that the
-- kernel writes for ``[[fig:]]`` and ``[[stat:]]`` markers, before ``--citeproc`` runs (pandoc-crossref is not
-- installed). Targets: a figure or a table with that identifier, or, in raw LaTeX, a ``\label{...}`` of it. LaTeX
-- output refers with ``\ref`` (LaTeX numbers the floats); any other output numbers figures and tables in document
-- order, as ``docx+native_numbering`` numbers their captions. A reference without a target is written as "??" and
-- reported on stderr as ``[WARNING] crossref: ...``, which the export treats as a failure.

local NAMES = { fig = "Figure", tbl = "Table" }
local numbers = {}

local function kind_of(id)
  local k = id:match("^(%a+):")
  if k and NAMES[k] then
    return k
  end
  return nil
end

local function collect(doc)
  local counts = { fig = 0, tbl = 0 }
  local function add(id)
    local k = id and kind_of(id)
    if k and numbers[id] == nil then
      counts[k] = counts[k] + 1
      numbers[id] = counts[k]
    end
  end
  doc:walk({
    Figure = function(f) add(f.identifier) end,
    Table = function(t) add(t.identifier) end,
    RawBlock = function(r)
      if r.format == "latex" or r.format == "tex" then
        for id in r.text:gmatch("\\label{([^}]+)}") do
          add(id)
        end
      end
    end,
  })
end

local function reference(id)
  local k = kind_of(id)
  if numbers[id] == nil then
    io.stderr:write("[WARNING] crossref: @" .. id .. " has no figure or table with that identifier\n")
    return pandoc.Str("??")
  end
  if FORMAT:match("latex") then
    return pandoc.RawInline("latex", NAMES[k] .. "~\\ref{" .. id .. "}")
  end
  return pandoc.Link(NAMES[k] .. " " .. tostring(numbers[id]), "#" .. id)
end

local function cite(c)
  local out = pandoc.List()
  for i, citation in ipairs(c.citations) do
    if not kind_of(citation.id) then
      return nil -- a bibliographic citation: citeproc renders it
    end
    if i > 1 then
      out:insert(pandoc.Str(";"))
      out:insert(pandoc.Space())
    end
    out:insert(reference(citation.id))
  end
  return out
end

function Pandoc(doc)
  collect(doc)
  return doc:walk({ Cite = cite })
end
