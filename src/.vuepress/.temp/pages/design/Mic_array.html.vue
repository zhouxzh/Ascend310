<template><div><h1 id="基于fpga-cpld的麦克风阵列信号" tabindex="-1"><a class="header-anchor" href="#基于fpga-cpld的麦克风阵列信号"><span>基于FPGA/CPLD的麦克风阵列信号</span></a></h1>
<h2 id="麦克风阵列介绍" tabindex="-1"><a class="header-anchor" href="#麦克风阵列介绍"><span>麦克风阵列介绍</span></a></h2>
<p>6+1麦克风阵列模块如下图所示，这个模块搭载了7个高品质的硅麦克风和12个可编程全彩LED，可以用于语音识别、声源定位和LED全彩显示等场合。</p>
<p><img src="@source/design/img2/mic.png" alt=""></p>
<h3 id="硅麦克风" tabindex="-1"><a class="header-anchor" href="#硅麦克风"><span>硅麦克风</span></a></h3>
<p>板载6+1个MSM261S4030H0R硅麦克风，支持左右声道输出，3左声道MIC（M0/M2/M4）+3右声道MIC（M1/M3/M5）+1右声道MIC（M6）。</p>
<ul>
<li>通讯接口：I2S</li>
<li>声压级：140DB SPL</li>
<li>灵敏度：-26（DB，DBFS@1kHz 1PA）</li>
<li>信噪比：57DB（20kHz bandwidth，A-weighted）</li>
<li>THD&lt;1%(100DB SPL@1kHz S=NOM,Rload&gt;2k)</li>
<li>时钟频率：1.0-4.0MHz（正常模式），150-800kHz（低功耗模式）</li>
</ul>
<h3 id="环形led参数" tabindex="-1"><a class="header-anchor" href="#环形led参数"><span>环形LED参数</span></a></h3>
<ul>
<li>板载12个级联的SK9822内置IC全彩LED，只需要2个IO就可以控制</li>
<li>通讯接口：双线同步控制信号CKI和SDI</li>
<li>可视角度：120°</li>
<li>双线同步选择正输出或负输出RGB三色LED输出</li>
<li>8位（256级）彩色设置，5位（32级）亮度调整</li>
<li>每个LED每种颜色消耗电流20mA，全颜色最高亮度消耗电流60mA。</li>
</ul>
<h3 id="板载接口" tabindex="-1"><a class="header-anchor" href="#板载接口"><span>板载接口</span></a></h3>
<p>2.54mm 2*5P牛角座 / 2.54mm单排针 / 0.5mm 10P FPC座</p>
<h3 id="产品尺寸" tabindex="-1"><a class="header-anchor" href="#产品尺寸"><span>产品尺寸</span></a></h3>
<p><img src="@source/design/img2/size.png" alt=""></p>
<h3 id="接口说明" tabindex="-1"><a class="header-anchor" href="#接口说明"><span>接口说明</span></a></h3>
<p><img src="@source/design/img2/socket.png" alt=""></p>
</div></template>


