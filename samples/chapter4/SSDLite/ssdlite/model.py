import torch
import torch.nn as nn
import timm


def _norm_layer(channels):
    return nn.BatchNorm2d(channels, eps=0.001, momentum=0.03)


def _prediction_block(in_channels, out_channels, kernel_size=3):
    return nn.Sequential(
        nn.Conv2d(
            in_channels,
            in_channels,
            kernel_size=kernel_size,
            padding=kernel_size // 2,
            groups=in_channels,
            bias=False,
        ),
        _norm_layer(in_channels),
        nn.ReLU6(inplace=True),
        nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=True),
    )


def _extra_block(in_channels, out_channels):
    intermediate_channels = out_channels // 2
    return nn.Sequential(
        nn.Conv2d(in_channels, intermediate_channels, kernel_size=1, bias=False),
        _norm_layer(intermediate_channels),
        nn.ReLU6(inplace=True),
        nn.Conv2d(
            intermediate_channels,
            intermediate_channels,
            kernel_size=3,
            stride=2,
            padding=1,
            groups=intermediate_channels,
            bias=False,
        ),
        _norm_layer(intermediate_channels),
        nn.ReLU6(inplace=True),
        nn.Conv2d(intermediate_channels, out_channels, kernel_size=1, bias=False),
        _norm_layer(out_channels),
        nn.ReLU6(inplace=True),
    )


def _normal_init(module):
    for layer in module.modules():
        if isinstance(layer, nn.Conv2d):
            torch.nn.init.normal_(layer.weight, mean=0.0, std=0.03)
            if layer.bias is not None:
                torch.nn.init.constant_(layer.bias, 0.0)


class MobileNet(nn.Module):
    def __init__(self, backbone='mobilenetv2', backbone_path=None, weights=True):
        super().__init__()

        name_map = {
            'mobilenetv1': 'mobilenetv1_100',
            'mobilenetv2': 'mobilenetv2_100',
            'mobilenetv3': 'mobilenetv3_large_100',
            'mobilenetv4': 'mobilenetv4_conv_small',
            'mobilenetv4_conv_aa_large': 'mobilenetv4_conv_aa_large',
            'mobilenetv4_conv_aa_medium': 'mobilenetv4_conv_aa_medium',
            'mobilenetv4_conv_blur_medium': 'mobilenetv4_conv_blur_medium',
            'mobilenetv4_conv_large': 'mobilenetv4_conv_large',
            'mobilenetv4_conv_medium': 'mobilenetv4_conv_medium',
            'mobilenetv4_conv_small': 'mobilenetv4_conv_small',
            'mobilenetv4_conv_small_035': 'mobilenetv4_conv_small_035',
            'mobilenetv4_conv_small_050': 'mobilenetv4_conv_small_050',
            'mobilenetv4_hybrid_large': 'mobilenetv4_hybrid_large',
            'mobilenetv4_hybrid_large_075': 'mobilenetv4_hybrid_large_075',
            'mobilenetv4_hybrid_medium': 'mobilenetv4_hybrid_medium',
            'mobilenetv4_hybrid_medium_075': 'mobilenetv4_hybrid_medium_075',
        }

        if backbone not in name_map:
            raise ValueError(
                f"Unsupported backbone: {backbone}. "
                f"Supported backbones: {list(name_map.keys())}"
            )
        timm_name = name_map[backbone]

        pretrained = weights if isinstance(weights, bool) else (weights is not None)
        if backbone_path:
            pretrained = False

        self.feature_extractor = timm.create_model(
            timm_name,
            pretrained=pretrained,
            features_only=True,
            out_indices=(3, 4) # 固定 16x/32x 两个特征层索引（按当前 timm 版本）
        )

        if backbone_path:
            self.feature_extractor.load_state_dict(torch.load(backbone_path, map_location='cpu'))

        ch = self.feature_extractor.feature_info.channels()  # [c20, c10]
        self.out_channels = [ch[0], ch[1], 512, 256, 256, 128]

    def forward(self, x):
        feats = self.feature_extractor(x)
        # 返回 20x20 和 10x10 两层
        return feats[0], feats[1]


class SSD320(nn.Module):
    def __init__(self, backbone=None, num_classes=81, aspect_ratios=None):
        super().__init__()
        if backbone is None:
            backbone = MobileNet('mobilenetv2')

        self.feature_extractor = backbone

        self.label_num = num_classes
        if aspect_ratios is None:
            aspect_ratios = [[2, 3] for _ in range(6)]
        self.aspect_ratios = aspect_ratios

        self._build_additional_features(self.feature_extractor.out_channels)
        self.num_defaults = [2 + 2 * len(ratios) for ratios in self.aspect_ratios]
        if len(self.num_defaults) != len(self.feature_extractor.out_channels):
            raise ValueError(
                f"num feature maps ({len(self.feature_extractor.out_channels)}) "
                f"!= num anchor levels ({len(self.num_defaults)})"
            )

        self.loc = []
        self.conf = []

        for nd, oc in zip(self.num_defaults, self.feature_extractor.out_channels):
            self.loc.append(_prediction_block(oc, nd * 4, 3))
            self.conf.append(_prediction_block(oc, nd * self.label_num, 3))

        self.loc = nn.ModuleList(self.loc)
        self.conf = nn.ModuleList(self.conf)
        self._init_weights()

    def _build_additional_features(self, input_size):
        self.additional_blocks = []
        # 关键：extra 从第二个 backbone 特征（10x10）开始接
        in_channels = input_size[1]
        for out_channels in input_size[2:]:
            layer = _extra_block(in_channels, out_channels)
            self.additional_blocks.append(layer)
            in_channels = out_channels

        self.additional_blocks = nn.ModuleList(self.additional_blocks)

    def _init_weights(self):
        _normal_init(self.additional_blocks)
        _normal_init(self.loc)
        _normal_init(self.conf)

    def bbox_view(self, src, loc, conf):
        ret = []
        for s, l, c in zip(src, loc, conf):
            ret.append((l(s).reshape(s.size(0), 4, -1), c(s).reshape(s.size(0), self.label_num, -1)))

        locs, confs = list(zip(*ret))
        locs, confs = torch.cat(locs, 2).contiguous(), torch.cat(confs, 2).contiguous()
        return locs, confs

    def get_detection_features(self, x):
        c4, c5 = self.feature_extractor(x)   # 20x20, 10x10
        detection_feed = [c4, c5]
        y = c5
        for l in self.additional_blocks:
            y = l(y)
            detection_feed.append(y)         # 5x5, 3x3, 2x2, 1x1
        return detection_feed

    def forward(self, x):
        detection_feed = self.get_detection_features(x)
        locs, confs = self.bbox_view(detection_feed, self.loc, self.conf)
        return locs, confs


class Loss(nn.Module):
    """
        Implements the loss as the sum of the followings:
        1. Confidence Loss: All labels, with hard negative mining
        2. Localization Loss: Only on positive labels
        Suppose input dboxes has the shape 12810x4
    """
    def __init__(self, dboxes):
        super(Loss, self).__init__()
        self.scale_xy = 1.0 / dboxes.scale_xy
        self.scale_wh = 1.0 / dboxes.scale_wh

        self.sl1_loss = nn.SmoothL1Loss(reduction='none')
        self.dboxes = nn.Parameter(dboxes(order="xywh").transpose(0, 1).unsqueeze(dim=0), requires_grad=False)
        self.con_loss = nn.CrossEntropyLoss(reduction='none')

    def _loc_vec(self, loc):
        gxy = self.scale_xy * (loc[:, :2, :] - self.dboxes[:, :2, :]) / self.dboxes[:, 2:, ]
        gwh = self.scale_wh * (loc[:, 2:, :] / self.dboxes[:, 2:, :]).log()
        return torch.cat((gxy, gwh), dim=1).contiguous()

    def forward(self, ploc, plabel, gloc, glabel):
        mask = glabel > 0
        pos_num = mask.sum(dim=1)

        vec_gd = self._loc_vec(gloc)

        sl1 = self.sl1_loss(ploc, vec_gd).sum(dim=1)
        sl1 = (mask.float() * sl1).sum(dim=1)

        con = self.con_loss(plabel, glabel)

        con_neg = con.clone()
        con_neg[mask] = 0
        _, con_idx = con_neg.sort(dim=1, descending=True)
        _, con_rank = con_idx.sort(dim=1)

        neg_num = torch.clamp(3 * pos_num, max=mask.size(1)).unsqueeze(-1)
        neg_mask = con_rank < neg_num

        closs = (con * ((mask + neg_mask).float())).sum(dim=1)

        total_loss = sl1 + closs
        num_mask = (pos_num > 0).float()
        pos_num = pos_num.float().clamp(min=1e-6)
        ret = (total_loss * num_mask / pos_num).mean(dim=0)
        return ret
