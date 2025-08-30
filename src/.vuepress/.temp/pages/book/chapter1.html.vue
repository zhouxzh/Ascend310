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
<li>等待软件格式化完成，并点击确定<img src="@source/book/img1/fmtfin.png" alt="格式化完成"></li>
</ol>
<h4 id="刷写系统到tf卡-以ubuntu为例" tabindex="-1"><a class="header-anchor" href="#刷写系统到tf卡-以ubuntu为例"><span>刷写系统到TF卡（以Ubuntu为例）</span></a></h4>
<blockquote>
<p>此处以Ubuntu为例</p>
</blockquote>
<ol>
<li>打开balenaEther，选择“从文件烧录”<img src="@source/book/img1/ether1.png" alt="balenaEther"></li>
<li>选择好要烧录的镜像文件（<strong>.img</strong>格式），再选择目标磁盘为TF卡对应的位置，如图中名称为“SDXC Card”的位置，选中并选择“选定1”。<img src="@source/book/img1/chooseether.png" alt="选择磁盘"></li>
<li>点击“现在烧录！”，耐心等待烧录完成。<img src="@source/book/img1/dd.png" alt="烧录过程"></li>
<li>烧录完成后进入校验过程，也请耐心等待。<img src="@source/book/img1/val.png" alt="校验过程"></li>
<li>烧录完成后即可关闭程序，并安全弹出TF卡<img src="@source/book/img1/finish.png" alt="完毕"></li>
</ol>
<h4 id="刷写系统到emmc" tabindex="-1"><a class="header-anchor" href="#刷写系统到emmc"><span>刷写系统到eMMC</span></a></h4>
<p>由于板上并不自带有eMMC模块，若要想使用需要额外购买香橙派的eMMC模块，此处暂时不列入参考，若需使用，请查阅香橙派的用户手册。</p>
<h4 id="刷写系统到ssd" tabindex="-1"><a class="header-anchor" href="#刷写系统到ssd"><span>刷写系统到SSD</span></a></h4>
<p>开发板带有M.2接口，可以使用SSD作为启动设备。但SSD需要自行准备，且根据香橙派</p>
<h4 id="调整设备启动方式的拨码开关" tabindex="-1"><a class="header-anchor" href="#调整设备启动方式的拨码开关"><span>调整设备启动方式的拨码开关</span></a></h4>
<p>开发板支持多种启动方式，包括TF卡、eMMC以及M.2 SSD，当这些存储设备都同时存在时，需要让开发板选定一个存储设备作为启动来源。
<img src="@source/book/img1/bootswitch.png" alt="boot开关">
两个开关都有左、右两种状态，因此共有4种状态，但是目前开发板仅使用3种模式，对应的参数表如下：</p>
<table>
<thead>
<tr>
<th style="text-align:center">Boot1开关</th>
<th style="text-align:center">Boot2开关</th>
<th style="text-align:center">启动设备</th>
</tr>
</thead>
<tbody>
<tr>
<td style="text-align:center">左</td>
<td style="text-align:center">左</td>
<td style="text-align:center">未使用</td>
</tr>
<tr>
<td style="text-align:center">右</td>
<td style="text-align:center">右</td>
<td style="text-align:center">TF卡</td>
</tr>
<tr>
<td style="text-align:center">左</td>
<td style="text-align:center">右</td>
<td style="text-align:center">eMMC</td>
</tr>
<tr>
<td style="text-align:center">右</td>
<td style="text-align:center">左</td>
<td style="text-align:center">M.2 SSD (Nvme或Ngff)</td>
</tr>
<tr>
<td style="text-align:center">切换拨码开关后，必须要将开发板完全断电再重新上电才能使新的启动配置生效，使用RESET按键重启则不会使新的启动配置生效。</td>
<td style="text-align:center"></td>
<td style="text-align:center"></td>
</tr>
</tbody>
</table>
<h3 id="启动开发板-ubuntu" tabindex="-1"><a class="header-anchor" href="#启动开发板-ubuntu"><span>启动开发板（Ubuntu）</span></a></h3>
<ul>
<li>图形化界面</li>
</ul>
<ol>
<li>将系统刷写完成的TF卡从读卡器中取出，插入开发板的TF卡插槽中，并确保两个启动开关的位置均在右边，接入HDMI数据线到靠近USB3.0接口的HDMI0接口，然后将Type-C电源线插入开发板最边缘的TYPE-C供电口，等待风扇的声音变小以及屏幕出现系统登录界面。
<img src="@source/book/img1/beforelogin.png" alt="登录"></li>
<li>进入登录界面后，将键盘接入开发板的USB接口中，默认的登录用户名是<code v-pre>HwHiAiUser</code>，输入该账户的密码<code v-pre>Mind@123</code>,登录进入系统。
<img src="@source/book/img1/logingui.png" alt="桌面">
默认账户表格：
| 用户名 | 密码 |
| :---: | :--: |
| root | Mind@123 |
| HwHiAiUser | Mind@123 |</li>
</ol>
<ul>
<li>串口界面</li>
</ul>
<ol>
<li>使用USB2TTL模块，与开发板的GPIO口进行连线<img src="@source/book/img1/gpio_ttl.png" alt="开发板串口">，开发板的TX（GPIO8）接入USB2TTL模块的RX接口，开发板的RX（GPIO10）则接入模块的TX接口，并连接好GND接地，在Windows电脑下可以使用PUTTY连接串口。</li>
<li>使用开发板自带的Micro USB接口进行串口调试，该方法更为方便，只需要一根Micro USB数据线，接入电脑后打开设备管理器查询对应的串口，然后使用PUTTY进行链接即可。
以Micro USB接口为例：</li>
<li>使用Micro USB数据线连接开发板和电脑</li>
<li>打开电脑的设备管理器，选择端口，寻找开发板对应的串口端口号<img src="@source/book/img1/ttl.png" alt="端口号"></li>
<li>打开串口调试软件（PUTTY）<img src="@source/book/img1/putty.png" alt="PUTTY">，将Connection Type选择为<code v-pre>Serial</code>，然后在Serial Line处将端口号修改为设备管理器中查到的端口号，如作者此处端口号为<code v-pre>COM3</code>，此外，还需要将Speed从9600修改为115200，最后点击Open打开串口。</li>
<li>等待出现<code v-pre>Ubuntu 22.04.3 LTS orangepiaipro ttyAM0</code>字样，输入登录的用户名HwHiAiUser并回车，然后输入密码Mind@123并回车，注意在输入密码的时候屏幕并不会显示任何东西，登陆后的界面如图所示。
<img src="@source/book/img1/serial.png" alt="串口">
<img src="@source/book/img1/login.png" alt="登录成功"></li>
</ol>
<h3 id="ubuntu-xfce桌面使用说明" tabindex="-1"><a class="header-anchor" href="#ubuntu-xfce桌面使用说明"><span>Ubuntu Xfce桌面使用说明</span></a></h3>
<p>目前系统仅支持Ubuntu 22.04 - Jammy系统，内核版本为Linux 5.10</p>
<h4 id="当前版本适配情况" tabindex="-1"><a class="header-anchor" href="#当前版本适配情况"><span>当前版本适配情况</span></a></h4>
<p>请详见香橙派官方的用户手册，有部分功能仅支持使用官方程序进行测试，无法直接从系统中调用，在使用过程中需注意这些限制。</p>
<h4 id="hdmi口使用" tabindex="-1"><a class="header-anchor" href="#hdmi口使用"><span>HDMI口使用</span></a></h4>
<p>开发板有两个HDMI2.0 接口，目前只有HDMI0 支持显示Linux 系统的桌面，当Linux 系统的桌面系统关闭时，HDMI0 和HDMI1 还可以用于NVR 二次开
发场景输出图片。</p>
<h4 id="音频使用" tabindex="-1"><a class="header-anchor" href="#音频使用"><span>音频使用</span></a></h4>
<p>Linux 内核没有适配耳机和HDMI 等的ALSA 音频驱动，此部分驱动还在开 发中，目前只能通过音频样例代码来测试耳机、HDMI 的音频播放和板载MIC 的录音功能。或者自行购买Linux系统免驱的USB外置声卡，经测试可以正常使用。</p>
</div></template>


