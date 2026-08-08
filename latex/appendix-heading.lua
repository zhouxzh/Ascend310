local stringify = require('pandoc.utils').stringify

function Header(header)
  if header.level ~= 1 then
    return nil
  end

  local title = stringify(header.content)
  local chapter_title = title:match('^附录%s*%d+%s*：%s*(.+)$')
    or title:match('^附录%s*%d+%s*:%s*(.+)$')
  if not chapter_title then
    return nil
  end

  local parsed = pandoc.read(chapter_title, 'markdown')
  if parsed.blocks[1] and parsed.blocks[1].content then
    header.content = parsed.blocks[1].content
  end

  return header
end
