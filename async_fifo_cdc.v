// Parameterized Asynchronous FIFO with Gray-Code Pointer Synchronization
module async_fifo_cdc #(
    parameter DATA_WIDTH = 8,
    parameter ADDR_WIDTH = 4
)(
    input  wire                  wclk,
    input  wire                  wrst_n,
    input  wire                  winc,
    input  wire [DATA_WIDTH-1:0] wdata,
    output wire                  wfull,

    input  wire                  rclk,
    input  wire                  rrst_n,
    input  wire                  rinc,
    output wire [DATA_WIDTH-1:0] rdata,
    output wire                  rempty
);

    localparam DEPTH = 1 << ADDR_WIDTH;

    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    reg [ADDR_WIDTH:0]   wptr, rptr;
    reg [ADDR_WIDTH:0]   wq2_rptr, wq1_rptr;
    reg [ADDR_WIDTH:0]   rq2_wptr, rq1_wptr;

    wire [ADDR_WIDTH:0]  wgray, rgray;
    wire [ADDR_WIDTH-1:0] waddr, raddr;

    // Binary to Gray
    assign wgray = wptr ^ (wptr >> 1);
    assign rgray = rptr ^ (rptr >> 1);

    assign waddr = wptr[ADDR_WIDTH-1:0];
    assign raddr = rptr[ADDR_WIDTH-1:0];

    // Memory Write
    always @(posedge wclk) begin
        if (winc && !wfull)
            mem[waddr] <= wdata;
    end

    // Read Data
    assign rdata = mem[raddr];

    // Pointer Increment & CDC Synchronization
    always @(posedge wclk or negedge wrst_n) begin
        if (!wrst_n) begin
            wptr <= 0;
            {wq2_rptr, wq1_rptr} <= 0;
        end else begin
            if (winc && !wfull) wptr <= wptr + 1;
            {wq2_rptr, wq1_rptr} <= {wq1_rptr, rgray};
        end
    end

    always @(posedge rclk or negedge rrst_n) begin
        if (!rrst_n) begin
            rptr <= 0;
            {rq2_wptr, rq1_wptr} <= 0;
        end else begin
            if (rinc && !rempty) rptr <= rptr + 1;
            {rq2_wptr, rq1_wptr} <= {rq1_wptr, wgray};
        end
    end

    // Flags
    assign rempty = (rq2_wptr == rgray);
    assign wfull  = (wq2_rptr == {~wgray[ADDR_WIDTH:ADDR_WIDTH-1], wgray[ADDR_WIDTH-2:0]});

endmodule
