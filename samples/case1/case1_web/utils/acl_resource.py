import acl

class AclResource:
    def __init__(self, device_id=0):
        self.device_id = device_id
        self.context = None
        self.stream = None

    def __enter__(self):
        # Initialize ACL
        ret = acl.init()
        if ret != 0:
            raise RuntimeError(f"acl.init failed: {ret}")

        # Set the device
        ret = acl.rt.set_device(self.device_id)
        if ret != 0:
            raise RuntimeError(f"acl.rt.set_device failed: {ret}")

        # Create a context
        self.context, ret = acl.rt.create_context(self.device_id)
        if ret != 0:
            raise RuntimeError(f"acl.rt.create_context failed: {ret}")

        # Create a stream
        self.stream, ret = acl.rt.create_stream()
        if ret != 0:
            raise RuntimeError(f"acl.rt.create_stream failed: {ret}")

        print("ACL resources initialized successfully.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Destroy the stream
        if self.stream:
            ret = acl.rt.destroy_stream(self.stream)
            if ret != 0:
                print(f"Warning: acl.rt.destroy_stream failed: {ret}")

        # Destroy the context
        if self.context:
            ret = acl.rt.destroy_context(self.context)
            if ret != 0:
                print(f"Warning: acl.rt.destroy_context failed: {ret}")

        # Reset the device
        ret = acl.rt.reset_device(self.device_id)
        if ret != 0:
            print(f"Warning: acl.rt.reset_device failed: {ret}")

        # Finalize ACL
        ret = acl.finalize()
        if ret != 0:
            print(f"Warning: acl.finalize failed: {ret}")

        print("ACL resources released.")