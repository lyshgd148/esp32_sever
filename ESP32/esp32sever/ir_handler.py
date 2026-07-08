import gc


def do_ir_learn(net):
    try:
        from esp32sever.infrared import IRLearner
        learner = IRLearner()
        data = learner.record(5000)
        gc.collect()
        if data:
            net.safe_send("IR_DATA=" +
                          ",".join(str(v) for v in data) + "\n")
        else:
            net.safe_send("IR_ERROR=超时, 未收到红外信号\n")
    except Exception as e:
        gc.collect()
        net.safe_send("IR_ERROR=%s\n" % str(e))


def do_ir_send(net, data_str):
    try:
        from esp32sever.infrared import IRLearner
        data = [int(x) for x in data_str.split(",") if x]
        IRLearner().send_raw(data)
        gc.collect()
        net.safe_send("IR_SEND_OK\n")
    except Exception as e:
        gc.collect()
        net.safe_send("IR_SEND_ERROR=%s\n" % str(e))
