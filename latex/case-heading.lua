-- Remove simple ordinal prefixes that would duplicate LaTeX section numbers.
function Header(header)
  local first = header.content[1]
  if not first or first.t ~= 'Str' then
    return nil
  end

  if not first.text:match('^%d+[%.、%)]$') then
    return nil
  end

  table.remove(header.content, 1)
  if header.content[1] and header.content[1].t == 'Space' then
    table.remove(header.content, 1)
  end

  return header
end
