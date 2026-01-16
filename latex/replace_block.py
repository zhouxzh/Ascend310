import os
import sys
import argparse

def replace_block_symbol(file_path):
    target_char = "█"
    # LaTeX 命令：灰色实心方块，使用 (*@ ... @*) 在 listing 中逃逸
    replacement = r"(*@\textcolor{gray}{\rule{1em}{1em}}@*)"
    # 为了让 listings 识别上述定界符，需要在文件开头插入配置
    lst_config = r"\lstset{escapeinside={(*@}{@*)}}"
    
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if target_char in content:
            # 1. 替换符号
            new_content = content.replace(target_char, replacement)
            
            # 2. 确保文件开头包含 escapeinside 配置（如果尚未包含）
            if lst_config not in new_content:
                new_content = lst_config + "\n" + new_content

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Successfully replaced symbols and added lstset in {file_path}")
        else:
            print(f"No target symbols found in {file_path}")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replace block symbol in LaTeX file.")
    parser.add_argument("file", help="Path to the target file")
    args = parser.parse_args()
    
    replace_block_symbol(args.file)
