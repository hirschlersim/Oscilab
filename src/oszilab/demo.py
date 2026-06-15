from oszilab.oscilab import Oscilab


def run():
    oszi = Oscilab()
    oszi.addTimeSeriesPlot("USB-Sensor")

    oszi.regSignal(label="channel-1", fs=100)

    oszi.link(subplot=1, label="channel-1")

    # # 1. Register the signals names definitions
    # osziolab.regSignal(label="channel1", fs=200)
    # oszilab.regSignal(label="channel2", fs=200)
    
    # # 2. Tell the system where they should be routed and painted
    # oszilab.plot("channel1", subplot=1)
    # oszilab.plot("channel2", subplot=2)

    oszi.show()


if __name__ == "__main__":
    run()