function Link(el)
  local target = el.target

  if target:match('^%a[%w+.-]*:') then
    return nil
  end

  local fragment = target:match('^[^#]+%.md#(.+)$')
  if fragment then
    el.target = '#' .. fragment
    return el
  end
end

local force_next_figure_here = false

function RawBlock(el)
  if el.format == 'html' and el.text:match('^%s*<!%-%-%s*pdf%-page%-break%s*%-%->%s*$') then
    return pandoc.RawBlock('latex', '\\clearpage')
  end

  if el.format == 'html' and el.text:match('^%s*<!%-%-%s*pdf%-figure%-here%s*%-%->%s*$') then
    force_next_figure_here = true
    return {}
  end
end

function Figure(el)
  if not force_next_figure_here then
    return nil
  end

  force_next_figure_here = false
  local latex = pandoc.write(pandoc.Pandoc({ el }), 'latex')
  latex = latex:gsub('\\begin{figure}', '\\begin{figure}[H]', 1)
  latex = latex:gsub('%./img3/', 'cases/img3/')
  return pandoc.RawBlock('latex', latex)
end

local function is_manual_heading_number(text)
  local part_count = 0

  for part in text:gmatch('[^.]+') do
    if not part:match('^%d+$') then
      return false
    end

    part_count = part_count + 1
  end

  return part_count >= 2
end

function Header(el)
  local first = el.content[1]

  if first and first.t == 'Str' then
    local manual_number = first.text:match('^(%d[%d.]*)%s+')

    if manual_number and is_manual_heading_number(manual_number) then
      first.text = first.text:gsub('^%d[%d.]*%s+', '', 1)
      el.content[1] = first
      return el
    end

    if is_manual_heading_number(first.text) then
      table.remove(el.content, 1)

      local next = el.content[1]
      if next and (next.t == 'Space' or next.t == 'SoftBreak') then
        table.remove(el.content, 1)
      end

      return el
    end
  end
end
