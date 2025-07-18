<template><div><h3 id="所需配件" tabindex="-1"><a class="header-anchor" href="#所需配件"><span>所需配件</span></a></h3>
<ol>
<li>
<p>TF卡
容量最小为32GB，速率为Class10级以上的闪迪品牌的TF卡，如下图所示。建议使用64G及以上的TF卡，以避免在开发过程中出现磁盘空间不足的问题。
<img src="@source/book/img1/tf.jpg" alt="tf卡"></p>
</li>
<li>
<p>TF卡读卡器
用于读写TF卡，刷写系统，建议选择速率为USB3.0以上的，减少系统刷写的等待时间。
<img src="@source/book/img1/reader.jpg" alt="读卡器"></p>
</li>
<li>
<p>HDMI线或HDMI转mini-HDMI线
主要取决于显示器的接口类型该开发板的视频输出接口为标准HDMI接口。
<img src="@source/book/img1/hdmi.jpg" alt="HDMI">
<img src="@source/book/img1/minihdmi.jpg" alt="Mini HDMI"></p>
</li>
<li>
<p>电源
该开发板的电源输入为PD 20V，需要搭配支持PD协议20V挡位的65W电源适配器。
<img src="@source/book/img1/power.png" alt="PD电源"></p>
</li>
<li>
<p>USB接口的鼠标以及键盘
在无远程访问的条件下对开发板进行本地调试。
<img src="@source/book/img1/mouse.png" alt="鼠标"></p>
</li>
<li>
<p>金属配套外壳
用于保护开发板硬件。
<img src="@source/book/img1/cover.png" alt="外壳"></p>
</li>
<li>
<p>12V散热风扇以及散热鳍块
开发板的风扇接口为2pin，输出电压为12v，支持PWM调速。由于该开发板的CPU发热较大，强烈建议安装主动扇热设备。
<img src="@source/book/img1/fan.png" alt="风扇"></p>
</li>
<li>
<p>Type-C转USB 3.0转接线（可选）
OrangePi AIPro开发板具有一个Type-C接口，协议为USB3.0（不支持USB 2.0），可外接支持USB3.0以上协议的外置设备。
<img src="@source/book/img1/otg.png" alt="转接线"></p>
</li>
<li>
<p>M.2接口 2280规格的PCIe Nvme SSD（可选）
开发板的背部设计有M.2接口，可外接一个M.2的SSD作为开发板的系统盘或者存储。
<img src="@source/book/img1/nvme.png" alt="nvme ssd"></p>
</li>
<li>
<p>M.2接口 2280规格的Sata Ngff SSD（可选）
同样，开发板的M.2接口不仅支持PCIe协议，也支持Sata协议，因此也可以使用Sata协议的SSD。
<img src="@source/book/img1/ngff.png" alt="ngff ssd"></p>
</li>
<li>
<p>香橙派的eMMC模块（可选）
eMMC（嵌入式多媒体卡）是一种集成了闪存和控制器的低成本存储解决方案，主要用于智能手机、平板电脑和低端笔记本电脑等消费电子产品。其读写速度适中（100-400MB/s），比传统机械硬盘快但不及固态硬盘（SSD），具有体积小、功耗低和易于集成的特点。开发板支持使用eMMC模块作为存储，但需要额外购置eMMC模块。
<img src="@source/book/img1/emmc1.png" alt="emmc正面">
<img src="@source/book/img1/emmc2.png" alt="emmc背面"></p>
</li>
<li>
<p>USB摄像头模块（可选）
可用于图像识别、视频通话等多方面用途。
<img src="@source/book/img1/camera.png" alt="摄像头"></p>
</li>
<li>
<p>网线（可选）
开发板自带有wifi模块可用于连接wifi，若需要更稳定的网络连接，建议使用网线连接。
<img src="@source/book/img1/cable.png" alt="网线"></p>
</li>
<li>
<p>树莓派IMX219型号摄像头（MIPI-CSI）（可选）
开发板带有两个MIPI-CSI接口，可以兼容树莓派的MIPI摄像头，无需占用USB接口。
<img src="@source/book/img1/csi.png" alt="MIPI-CSI摄像头"></p>
</li>
<li>
<p>树莓派5寸MIPI LCD显示屏（可选）
开发板带有一个MIPI-DSI显示输出接口，可以直接驱动MIPI的显示屏，而无需外接显示器。
<img src="@source/book/img1/dsi.png" alt="MIPI显示器"></p>
</li>
<li>
<p>Micro USB数据线（可选）
开发板自带了CH343P芯片，将UART转发为Micro USB接口，若需要使用串口对开发板进行调试，则需要使用Micro USB数据线。
<img src="@source/book/img1/microusb.png" alt="Micro USB数据线"></p>
</li>
</ol>
<h3 id="下载开发板的系统镜像" tabindex="-1"><a class="header-anchor" href="#下载开发板的系统镜像"><span>下载开发板的系统镜像</span></a></h3>
<p>作为华为生态中重要的一员，开发板不仅支持Ubuntu系统，也支持openEuler系统，但由于开发板自身并无存储，我们在使用开发板的过程中需要使用电脑对TF卡进行系统的刷写，建议使用安装有Windows11 或 Ubuntu22.04以上版本的PC。</p>
<p>首先，打开香橙派官网的<a href="http://www.orangepi.cn/html/hardWare/computerAndMicrocontrollers/service-and-support/Orange-Pi-AIpro.html" target="_blank" rel="noopener noreferrer">技术支持界面</a>。<img src="@source/book/img1/技术支持.png" alt="技术支持页面"></p>
<p>向下滑动网页，找到官方镜像部分，分为Ubuntu和openEuler两个部分，两个系统都是官方为我们编译完成的，且预装了部分昇腾NPU的应用环境以及软件，非常方便新手用户上手使用。
<img src="@source/book/img1/官方镜像.png" alt="官方镜像"></p>
<h4 id="ubuntu" tabindex="-1"><a class="header-anchor" href="#ubuntu"><span>Ubuntu</span></a></h4>
<ol>
<li>点击下载<img src="@source/book/img1/download_ubuntu.png" alt="下载"></li>
<li>复制提取码并跳转<img src="@source/book/img1/copyandjump.png" alt="跳转"></li>
<li>打开百度网盘的链接后有一个命名为Ubuntu的文件夹，点开该文件夹<img src="@source/book/img1/folder.png" alt="文件夹"></li>
<li>文件夹中，后缀为.xz的文件是镜像压缩包文件，.sha文件是压缩包的md5校验码文件，用于校验镜像包文件是否完整。</li>
<li>文件夹中的镜像有两种，一种文件名带有Desktop的，是带有GUI图形化界面的，另一种文件名带有minimal的，是不具有图形化界面的，只有命令行界面。建议新学习的用户使用带有desktop的镜像。 <img src="@source/book/img1/chooseubuntu.png" alt="选择镜像"></li>
<li>下载后先校验压缩包是否完整，后解压压缩包</li>
</ol>
<h4 id="openeuler" tabindex="-1"><a class="header-anchor" href="#openeuler"><span>openEuler</span></a></h4>
<ol>
<li>点击下载<img src="@source/book/img1/download_openeuler.png" alt="下载"></li>
<li>复制提取码并跳转<img src="@source/book/img1/cpjp.png" alt="跳转"></li>
<li>打开百度网盘的链接后有一个命名为OpenEuler的文件夹，点开该文件夹<img src="@source/book/img1/folderr.png" alt="文件夹"></li>
<li>文件夹中，后缀为.xz的文件是镜像压缩包文件，.sha文件是压缩包的md5校验码文件，用于校验镜像包文件是否完整。</li>
<li>文件夹中的镜像只有一种，即具有GUI图形化界面的openEuler系统。<img src="@source/book/img1/chooseeuler.png" alt="OpenEuler"></li>
<li>下载后先校验压缩包是否完整，后解压压缩包</li>
</ol>
<h4 id="使用md5校验下载的文件" tabindex="-1"><a class="header-anchor" href="#使用md5校验下载的文件"><span>使用md5校验下载的文件</span></a></h4>
<p>在Windows系统下，可以使用<code v-pre>certutil -hashfile &lt;filename&gt; md5</code>;在Ubuntu系统下，可以使用<code v-pre>md5sum &lt;filename&gt;</code>；在MacOS系统下，可以使用<code v-pre>md5 &lt;filename&gt;</code>进行计算，此处以Windows系统为例：在文件夹按住Shift键并单击鼠标右键，选择“在终端（Powershell/命令提示符）中打开”<img src="@source/book/img1/shell.png" alt="终端">，然后在打开的窗口中输入<code v-pre>certutil -hashfile opiaipro_ubuntu22.04_desktop_aarch64_20241128.img.xz md5</code><img src="@source/book/img1/md5.png" alt="md5校验">，将得到的md5值与<code v-pre>opiaipro_ubuntu22.04_desktop_aarch64_20241128.img.xz.sha</code>文件进行对比，若一致可进行下一步操作，否则需要重新下载。</p>
<h3 id="刷写系统到tf卡" tabindex="-1"><a class="header-anchor" href="#刷写系统到tf卡"><span>刷写系统到TF卡</span></a></h3>
<h4 id="下载并安装必要的工具" tabindex="-1"><a class="header-anchor" href="#下载并安装必要的工具"><span>下载并安装必要的工具</span></a></h4>
<blockquote>
<p>下载链接：<a href="http://www.orangepi.cn/html/hardWare/computerAndMicrocontrollers/service-and-support/Orange-Pi-AIpro.html" target="_blank" rel="noopener noreferrer">官网</a> <a href="https://pan.baidu.com/s/1Jho73pw91r5GJD2KijY45Q?pwd=3xuz#list/path=%2F" target="_blank" rel="noopener noreferrer">百度网盘</a></p>
</blockquote>
<ol>
<li>SD Card Formatter
这个是TF卡的快速格式化工具，在每次需要刷写系统之前，都必须先对TF卡进行格式化操作，若不格式化在后续的刷写系统过程中有较大概率出错。</li>
<li>balenaEther
这个是系统镜像的刷写工具，用于刷写img镜像文件进入TF卡。</li>
</ol>
<h4 id="格式化tf卡" tabindex="-1"><a class="header-anchor" href="#格式化tf卡"><span>格式化TF卡</span></a></h4>
<ol>
<li>将TF卡插入读卡器中，并将读卡器插入电脑</li>
<li>打开SD Card Formatter软件<img src="@source/book/img1/SDFmt.png" alt="TF卡格式化"></li>
<li>点击右下角Format按键，格式化TF卡<img src="@source/book/img1/fmt.png" alt="格式化"></li>
</ol>
<blockquote>
<p>警告内容是关于格式化操作会清除TF卡上原有的所有数据，此处选是<img src="@source/book/img1/warning.png" alt="warning"></p>
</blockquote>
<ol start="4">
<li>等待软件格式化完成</li>
</ol>
<h4 id="刷写系统到tf卡-以ubuntu为例" tabindex="-1"><a class="header-anchor" href="#刷写系统到tf卡-以ubuntu为例"><span>刷写系统到TF卡（以Ubuntu为例）</span></a></h4>
<blockquote>
<p>此处以Ubuntu为例</p>
</blockquote>
<ol>
<li>打开balenaEther，选择“从文件烧录”<img src="@source/book/img1/ether1.png" alt="balenaEther"></li>
<li>选择好要烧录的镜像文件（<strong>.img</strong>格式），再选择目标磁盘为TF卡对应的位置，如图中名称为“SDXC Card”的位置，选中并选择“选定1”。<img src="@source/book/img1/chooseether.png" alt="选择磁盘"></li>
<li>点击“现在烧录！”，耐心等待烧录完成。<img src="@source/book/img1/dd.png" alt="烧录过程"></li>
</ol>
<h3 id="启动开发板-ubuntu" tabindex="-1"><a class="header-anchor" href="#启动开发板-ubuntu"><span>启动开发板（Ubuntu）</span></a></h3>
<p>将系统刷写完成的TF卡从读卡器中</p>
</div></template>


