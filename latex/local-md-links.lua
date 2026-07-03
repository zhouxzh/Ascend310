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
