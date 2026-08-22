from oscilab.oscilab import Oscilab

def run():

    oszi = Oscilab()
    oszi.addTimeSeriesPlot("USB-Sensor")
    oszi.addTimeSeriesPlot("Test-ME")

    # # 1. Register the signals names definitions
    oszi.regSignal(label="channel-1", fs=100)

    # # 2. Tell the system where they should be routed and painted
    oszi.link(subplot=1, label="channel-1")


    oszi.show()


if __name__ == "__main__":
    run()
