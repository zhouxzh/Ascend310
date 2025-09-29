### 这个在网页版vuepress中生效
<picture>
  <img src="/experiment/img0/orangepi-release.png" alt="orangepi-release" width="500" height="500">
</picture>

### 以下四个都不生效
<!-- 新语法 -->
![orangepi-release =500x500](img0/orangepi-release.png)

<!-- Obsidian语法 -->
![orangepi-release|500x500](img0/orangepi-release.png)

<!-- 只设置宽度 -->
![orangepi-release =500x](img0/orangepi-release.png)

<!-- 只设置高度 -->
![orangepi-release =x500](img0/orangepi-release.png)

### 这个在pandoc转latex过程中生效
![orangepi-release](img0/orangepi-release.png){ width=50% }