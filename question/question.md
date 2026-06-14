# 昇腾310B常见问题汇总

1. Gitee仓库跟Gitcode仓库
目前Gitee仓库说代码都已经迁移到gitcode。但是官方文档大量的链接全部都是指向gitee的。


2. CANN版本问题
CANN版本兼容性的问题，由于CANN版本更新太快了，而且版本之间不兼容。
现在还有一个问题，就是对于香橙派的系统，默认安装了CANN8.0，但是我安装了CANN8.5之后，调用atc工具，发现atc工具指向的环境变量还是原来的CANN8.0。但是CANN8.5安装文件的名字都不同了，安装包之间路径不兼容，然后没有详细说明，如果更新CANN版本问题，导致安装恨难，很多程序更换CANN版本后，不能正常运行。目前也没有一个迁移的说明。
CANN8.5之后，安装的方法也改变了。
我用香橙派系统的旧版本的系统，CANN版本是7.0。我可以下载8.3的run文件，可以正常的安装。但是升级到8.5容易出问题。
CANN8.5提供的run文件为：Ascend-cann-310b-ops_8.5.2_linux-aarch64.run和Ascend-cann-nnal_8.5.2_linux-aarch64.run
CANN8.3提供的run文件为：
Ascend-cann-kernels-310b_8.3.RC2_linux-aarch64.run。
我估计出错是以为ops的环境变量的设置问题。但是没有一个迁移说明，即使使用各种AI工具，也是很难自动化的迁移。

3. atc工具环境变量问题
对于昇腾310B来说，使用atc工具，默认是开启多线程的，而且线程的数量默认为8，对于只有8G内存的昇腾310B来说，几乎所有的模型转换都会出现内存错误。同时环境变量有两个，一个是TE_PARALLEL_COMPILER，一个是TE_PARALLEL_COMPILER，这两个环境变量的对于不同的CANN版本租用还是不同的。

4. AMCT_ONNX工具

AMCT_ONNX工具强烈依赖于CANN版本，还有onnxruntime版本，官方的说明文档https://www.hiascend.com/document/detail/zh/canncommercial/80RC1/devaids/auxiliarydevtool/atlasamctonnx_16_0013.html，这个是搜索出来的，是CANN8.0的版本，然后我电机网页上头的CANN版本切换，我如果切换到8.3，就不知道切换到哪里去了，反正不是AMCT_ONNX的安装依赖。

然后安装前的依赖的网页https://www.hiascend.com/document/detail/zh/canncommercial/80RC1/devaids/auxiliarydevtool/atlasamctonnx_16_0013.html,这个网页有一个表格，这个表格，只是罗列了所需要的依赖，然后看完我还是不知道具体的依赖版本号。然后这个表格甚至是前端渲染出来的表格，我直接想用codex获取这个网页来分析版本依赖，也是很麻烦。
Vibe Programing时代，简单的网页内容有利于AI获取信息，提高效率。