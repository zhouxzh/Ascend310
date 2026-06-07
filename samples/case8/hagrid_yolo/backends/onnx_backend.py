class OnnxBackend:
    def __init__(self, model_path, provider="CPUExecutionProvider", intra_op_threads=3, inter_op_threads=1):
        import onnxruntime as ort

        available = ort.get_available_providers()
        providers = [provider] if provider in available else ["CPUExecutionProvider"]
        if provider not in available:
            print(f"Provider {provider} is not available. Using CPUExecutionProvider.")

        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = max(1, intra_op_threads)
        session_options.inter_op_num_threads = max(1, inter_op_threads)
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(str(model_path), sess_options=session_options, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def infer(self, input_tensor):
        return [self.session.run([self.output_name], {self.input_name: input_tensor})[0]]

    def release(self):
        self.session = None

