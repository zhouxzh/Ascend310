import os
import numpy as np
from datasets import load_dataset
from tqdm import tqdm
import argparse
import time
import json
import acl
from utils_cpu import dboxes300_coco, Encoder, visualize_sample
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2


def _check_ret(ret, msg):
    if ret != 0:
        raise RuntimeError(f"{msg} failed, ret={ret}")


class AclModelRunner:
    def __init__(self, model_path, device_id=0):
        self.model_path = model_path
        self.device_id = device_id

        self.context = None
        self.stream = None
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers = []
        self.output_buffers = []
        self.output_shapes = []

        self._init_acl()
        self._load_model()
        self._prepare_io_buffers()

    def _init_acl(self):
        ret = acl.init()
        if ret not in (0, 100002):
            raise RuntimeError(f"acl.init failed, ret={ret}")

        ret = acl.rt.set_device(self.device_id)
        _check_ret(ret, "acl.rt.set_device")

        self.context, ret = acl.rt.create_context(self.device_id)
        _check_ret(ret, "acl.rt.create_context")

        self.stream, ret = acl.rt.create_stream()
        _check_ret(ret, "acl.rt.create_stream")

    def _load_model(self):
        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        _check_ret(ret, "acl.mdl.load_from_file")

        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        _check_ret(ret, "acl.mdl.get_desc")

    def _get_output_shape(self, output_idx):
        dims_info, ret = acl.mdl.get_output_dims(self.model_desc, output_idx)
        if ret != 0:
            return None

        dim_count = dims_info.get("dimCount", 0)
        dims = dims_info.get("dims", [])
        if dim_count <= 0 or not dims:
            return None

        shape = tuple(int(x) for x in dims[:dim_count])
        if np.prod(shape) <= 0:
            return None
        return shape

    def _prepare_io_buffers(self):
        self.input_dataset = acl.mdl.create_dataset()
        self.output_dataset = acl.mdl.create_dataset()

        input_num = acl.mdl.get_num_inputs(self.model_desc)
        output_num = acl.mdl.get_num_outputs(self.model_desc)

        for idx in range(input_num):
            input_size = acl.mdl.get_input_size_by_index(self.model_desc, idx)
            input_ptr, ret = acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc input[{idx}]")

            input_buf = acl.create_data_buffer(input_ptr, input_size)
            ret = acl.mdl.add_dataset_buffer(self.input_dataset, input_buf)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer input[{idx}]")

            self.input_buffers.append({
                "ptr": input_ptr,
                "size": input_size,
                "buffer": input_buf,
            })

        for idx in range(output_num):
            output_size = acl.mdl.get_output_size_by_index(self.model_desc, idx)
            output_ptr, ret = acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
            _check_ret(ret, f"acl.rt.malloc output[{idx}]")

            output_buf = acl.create_data_buffer(output_ptr, output_size)
            ret = acl.mdl.add_dataset_buffer(self.output_dataset, output_buf)
            _check_ret(ret, f"acl.mdl.add_dataset_buffer output[{idx}]")

            self.output_buffers.append({
                "ptr": output_ptr,
                "size": output_size,
                "buffer": output_buf,
            })
            self.output_shapes.append(self._get_output_shape(idx))

    def infer(self, input_np):
        if not isinstance(input_np, np.ndarray):
            raise TypeError("input_np must be numpy.ndarray")

        input_np = np.ascontiguousarray(input_np.astype(np.float32))
        input_bytes = input_np.tobytes()

        first_input = self.input_buffers[0]
        if len(input_bytes) > first_input["size"]:
            raise ValueError(
                f"Input bytes {len(input_bytes)} exceed model input size {first_input['size']}"
            )

        host_in_ptr = acl.util.bytes_to_ptr(input_bytes)
        ret = acl.rt.memcpy(
            first_input["ptr"],
            first_input["size"],
            host_in_ptr,
            len(input_bytes),
            ACL_MEMCPY_HOST_TO_DEVICE,
        )
        _check_ret(ret, "acl.rt.memcpy host_to_device")

        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        _check_ret(ret, "acl.mdl.execute")

        outputs = []
        for idx, out in enumerate(self.output_buffers):
            host_out = np.zeros(out["size"], dtype=np.uint8)
            host_out_ptr = acl.util.numpy_to_ptr(host_out)

            ret = acl.rt.memcpy(
                host_out_ptr,
                out["size"],
                out["ptr"],
                out["size"],
                ACL_MEMCPY_DEVICE_TO_HOST,
            )
            _check_ret(ret, f"acl.rt.memcpy device_to_host output[{idx}]")

            tensor = np.frombuffer(host_out.tobytes(), dtype=np.float32)
            shape = self.output_shapes[idx]
            if shape is not None and int(np.prod(shape)) == tensor.size:
                tensor = tensor.reshape(shape)
            outputs.append(tensor)

        return outputs

    def release(self):
        if self.input_dataset is not None:
            for buf in self.input_buffers:
                acl.destroy_data_buffer(buf["buffer"])
                acl.rt.free(buf["ptr"])
            acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None

        if self.output_dataset is not None:
            for buf in self.output_buffers:
                acl.destroy_data_buffer(buf["buffer"])
                acl.rt.free(buf["ptr"])
            acl.mdl.destroy_dataset(self.output_dataset)
            self.output_dataset = None

        if self.model_desc is not None:
            acl.mdl.destroy_desc(self.model_desc)
            self.model_desc = None

        if self.model_id is not None:
            acl.mdl.unload(self.model_id)
            self.model_id = None

        if self.stream is not None:
            acl.rt.destroy_stream(self.stream)
            self.stream = None

        if self.context is not None:
            acl.rt.destroy_context(self.context)
            self.context = None

        acl.rt.reset_device(self.device_id)
        acl.finalize()


def pick_locs_confs(outputs):
    if len(outputs) != 2:
        raise RuntimeError(f"SSD expects 2 outputs, got {len(outputs)}")

    out0, out1 = outputs[0], outputs[1]

    def is_loc_tensor(tensor):
        return tensor.ndim >= 2 and 4 in tensor.shape

    if is_loc_tensor(out0) and not is_loc_tensor(out1):
        return out0.astype(np.float32), out1.astype(np.float32)
    if is_loc_tensor(out1) and not is_loc_tensor(out0):
        return out1.astype(np.float32), out0.astype(np.float32)

    if out0.size <= out1.size:
        return out0.astype(np.float32), out1.astype(np.float32)
    return out1.astype(np.float32), out0.astype(np.float32)


def get_coco_ground_truth(val_ds_hf):
    print("Preparing COCO Ground Truth from HF dataset...")
    coco_gt_dict = {"images": [], "annotations": [], "categories": []}
    cat_ids = set()

    for i in range(len(val_ds_hf)):
        item = val_ds_hf[i]
        img_id = item.get("image_id", i)
        w, h = item["image"].size
        coco_gt_dict["images"].append({"id": img_id, "width": 300, "height": 300})

        objects = item.get("objects", {})
        if len(objects.get("bbox", [])) > 0:
            for bbox, cat in zip(objects["bbox"], objects["category"]):
                xmin, ymin, bw_src, bh_src = bbox

                bx = xmin * 300 / w
                by = ymin * 300 / h
                bw = bw_src * 300 / w
                bh = bh_src * 300 / h

                coco_gt_dict["annotations"].append(
                    {
                        "id": len(coco_gt_dict["annotations"]),
                        "image_id": img_id,
                        "category_id": cat + 1,
                        "bbox": [bx, by, bw, bh],
                        "area": bw * bh,
                        "iscrowd": 0,
                    }
                )
                cat_ids.add(cat + 1)

    for cid in cat_ids:
        coco_gt_dict["categories"].append({"id": cid, "name": str(cid)})

    gt_path = "coco_gt_temp.json"
    with open(gt_path, "w") as f:
        json.dump(coco_gt_dict, f)

    return COCO(gt_path)


def load_coco_val():
    dataset_name = "zhouxzh/coco-val"
    cache_directory = "./data"
    print(f"正在加载验证数据集: {dataset_name} ...")
    dataset = load_dataset(dataset_name, split="val", cache_dir=cache_directory)
    return dataset


def preprocess(image, img_size=300):
    if image.mode != "RGB":
        image = image.convert("RGB")

    orig_w, orig_h = image.size
    image_resized = image.resize((img_size, img_size))

    img_array = np.array(image_resized, dtype=np.float32) / 255.0
    img_array = img_array.transpose(2, 0, 1)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

    img_tensor = (img_array - mean) / std
    img_numpy = np.expand_dims(img_tensor, axis=0)
    return img_numpy, orig_w, orig_h


def get_gt_data(item, orig_w, orig_h):
    objects = item["objects"]
    if len(objects["bbox"]) == 0:
        return np.array([]), np.array([])

    boxes = np.array(objects["bbox"])
    labels = np.array(objects["category"]) + 1

    scale_x = 300.0 / orig_w
    scale_y = 300.0 / orig_h

    boxes[:, 0] *= scale_x
    boxes[:, 1] *= scale_y
    boxes[:, 2] *= scale_x
    boxes[:, 3] *= scale_y

    return boxes.astype(np.float32), labels.astype(np.int64)


def visualize_validation_predictions(args, runner, encoder, val_dataset):
    print("正在选取前 10 个样本进行连续推理和可视化...")
    indices = range(10)

    cats = val_dataset.features["objects"]["category"].feature.names
    cats = ["background"] + cats

    save_folder = f"viz_results/inference/{args.backbone}"
    os.makedirs(save_folder, exist_ok=True)

    for idx in indices:
        item = val_dataset[idx]
        image = item["image"]
        image_id = item["image_id"]

        img_input, orig_w, orig_h = preprocess(image)
        img_tensor = img_input[0]

        outs = runner.infer(img_input)
        locs, confs = pick_locs_confs(outs)

        results = encoder.decode_batch(locs, confs, criteria=0.5, max_output=200)
        p_boxes, p_labels, p_scores = results[0]

        gt_boxes, gt_labels = get_gt_data(item, orig_w, orig_h)

        save_path = f"{save_folder}/vis_{idx}.jpg"
        visualize_sample(
            img_tensor,
            gt_boxes,
            gt_labels,
            p_boxes,
            p_labels,
            p_scores,
            cats,
            save_path,
        )
        print(f"已处理图片 ID: {image_id}, 结果保存至 {save_path}")


def evaluate_dataset(val_dataset, runner, encoder, gt_file):
    print("\n开始评估全量数据集 mAP 和 推理帧率 ...")
    results = []

    start_time = time.time()
    inference_process_time = 0.0

    for item in tqdm(val_dataset, desc="Evaluating"):
        image = item["image"]
        image_id = item["image_id"]

        t0 = time.time()
        img_input, _, _ = preprocess(image)

        outs = runner.infer(img_input)
        locs, confs = pick_locs_confs(outs)

        decoded_results = encoder.decode_batch(locs, confs, criteria=0.5, max_output=200)
        inference_process_time += time.time() - t0

        p_boxes, p_labels, p_scores = decoded_results[0]

        if p_boxes.size > 0:
            p_boxes = p_boxes.copy() * 300.0
            p_boxes[:, 2] -= p_boxes[:, 0]
            p_boxes[:, 3] -= p_boxes[:, 1]

            for box, label, score in zip(p_boxes.tolist(), p_labels.tolist(), p_scores.tolist()):
                results.append(
                    {
                        "image_id": image_id,
                        "category_id": label,
                        "bbox": [round(x, 3) for x in box],
                        "score": round(score, 5),
                    }
                )

    total_time = time.time() - start_time
    count = len(val_dataset)
    fps_total = count / total_time
    fps_inference = count / inference_process_time if inference_process_time > 0 else 0

    print("\n================ 性能测试结果 ================")
    print(f"处理图片数量: {count}")
    print(f"总耗时: {total_time:.2f}s")
    print(f"全流程 FPS: {fps_total:.2f}")
    print(f"纯推理+解码 FPS: {fps_inference:.2f}")
    print("============================================")

    res_file = "inference_results.json"
    with open(res_file, "w") as f:
        json.dump(results, f)

    if os.path.exists(gt_file):
        try:
            print(f"正在使用 {gt_file} 计算 mAP ...")
            coco_gt = COCO(gt_file)
            coco_dt = coco_gt.loadRes(res_file)

            coco_eval = COCOeval(coco_gt, coco_dt, "bbox")
            img_ids = sorted([item["image_id"] for item in val_dataset])
            coco_eval.params.imgIds = img_ids

            coco_eval.evaluate()
            coco_eval.accumulate()
            coco_eval.summarize()

        except ImportError:
            print("未检测到 pycocotools，无法计算 mAP。")
        except Exception as e:
            print(f"计算 mAP 时发生错误: {e}")
    else:
        print(f"未找到 GT 文件 {gt_file}，无法计算 mAP。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="resnet50", help="backbone name")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id")
    parser.add_argument("--model", default="", help="OM model path, e.g. models/ssd_resnet50.om")
    args = parser.parse_args()

    val_dataset = load_coco_val()

    om_model_path = args.model if args.model else f"models/ssd_{args.backbone}.om"

    dboxes = dboxes300_coco()
    encoder = Encoder(dboxes)

    if not os.path.exists(om_model_path):
        print(f"OM 模型文件不存在: {om_model_path}")
        exit(1)

    runner = None
    try:
        runner = AclModelRunner(om_model_path, device_id=args.device)
        visualize_validation_predictions(args, runner, encoder, val_dataset)

        gt_file = "coco_gt_temp.json"
        get_coco_ground_truth(val_dataset)
        evaluate_dataset(val_dataset, runner, encoder, gt_file)
    except Exception as e:
        print(f"执行失败: {e}")
        exit(1)
    finally:
        if runner is not None:
            runner.release()
