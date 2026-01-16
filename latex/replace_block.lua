--[[
Pandoc Lua 过滤器：将 Unicode 全角实心方块 (U+2588) 替换为 LaTeX 命令。
--]]

function Str(el)
    -- 检查字符串内容中是否包含目标字符 U+2588 "█"
    if el.text:find("█") then
        -- 使用 gsub 将所有 "█" 替换为 LaTeX 命令
        -- 这里使用 \textcolor{gray}{\rule{1em}{1em}} 生成一个灰色实心方块
        -- 您可以根据需要调整颜色（gray）和大小（1em）
        local new_text = el.text:gsub("█", "\\textcolor{gray}{\\rule{1em}{1em}}")
        -- 仅在 LaTeX 输出时替换，否则返回原始字符串
        if FORMAT:match("latex") then
            return pandoc.RawInline('latex', new_text)
        else
            return pandoc.Str(el.text)
        end
    end
    -- 如果不包含目标字符，则原样返回该元素
    return el
end
